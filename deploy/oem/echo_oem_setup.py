#!/usr/bin/env python3
"""One-time local-account provisioning for the first Echo OS boot."""

from __future__ import annotations

import contextlib
import getpass
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

ACCOUNT = "echo"
STATE_DIRECTORY = Path("/var/lib/echo-os")
COMPLETE_MARKER = STATE_DIRECTORY / "oem-complete.json"
SHADOW_STATE = STATE_DIRECTORY / "local-account.shadow"
REGION_STATE_TOOL = Path("/usr/lib/echo-os/echo-region-state")
OEM_CREDENTIAL_NAME = "echo.os.oem"
OEM_CREDENTIAL_SCHEMA = 1
MAX_OEM_CREDENTIAL_SIZE = 8192
OEM_CREDENTIAL_KEYS = {
    "schema",
    "display_name",
    "hostname",
    "password",
    "locale",
    "keymap",
    "timezone",
}
MAX_DEVICE_NAME_LENGTH = 15
HOSTNAME_PATTERN = re.compile(
    rf"^[a-z0-9](?:[a-z0-9-]{{0,{MAX_DEVICE_NAME_LENGTH - 2}}}[a-z0-9])?$"
)
PASSWORD_HASH_PATTERN = re.compile(r"^\$[A-Za-z0-9./]+\$[A-Za-z0-9./$=,_-]+$")
IMAGE_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$")
MAX_ATTEMPTS = 5


class InputError(ValueError):
    """A user-facing validation error."""


def validate_display_name(raw: str) -> str:
    value = unicodedata.normalize("NFC", raw).strip()
    if not 1 <= len(value) <= 64:
        raise InputError("display name must contain 1 to 64 characters")
    if any(delimiter in value for delimiter in (":", ",")) or any(
        unicodedata.category(char).startswith("C") for char in value
    ):
        raise InputError("display name contains an unsupported delimiter or control character")
    return value


def validate_hostname(raw: str) -> str:
    value = raw.strip().lower()
    if not HOSTNAME_PATTERN.fullmatch(value):
        raise InputError(
            "device name must contain 1 to 15 lowercase letters, digits or internal hyphens "
            "so SMB clients use the same untruncated identity"
        )
    return value


def validate_password(raw: str) -> str:
    if not 12 <= len(raw) <= 256:
        raise InputError("password must contain 12 to 256 characters")
    if any(unicodedata.category(char).startswith("C") for char in raw):
        raise InputError("password contains unsupported control characters")
    folded = raw.casefold()
    if any(token in folded for token in ("password", "echo", "echo-os")):
        raise InputError("password is too predictable")
    if len(set(raw)) < 4:
        raise InputError("password must contain at least four distinct characters")
    return raw


def validate_region_credential_value(kind: str, raw: object) -> str:
    if not isinstance(raw, str):
        raise InputError(f"OEM {kind} must be text")
    value = raw.strip()
    if not 1 <= len(value) <= 128 or any(
        unicodedata.category(char).startswith("C") for char in value
    ):
        raise InputError(f"OEM {kind} has an invalid length or control character")
    return value


def require_credential_text(field: str, raw: object) -> str:
    if not isinstance(raw, str):
        raise InputError(f"OEM {field} must be text")
    return raw


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"OEM credential contains a duplicate field: {key}")
        result[key] = value
    return result


def read_oem_credential() -> dict[str, str] | None:
    """Read an optional systemd credential without accepting a normal file path."""
    credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not credentials_directory:
        return None
    directory = Path(credentials_directory)
    if not directory.is_absolute():
        raise InputError("systemd credentials directory must be absolute")
    credential_path = directory / OEM_CREDENTIAL_NAME
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(credential_path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise InputError("unable to open the OEM system credential") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise InputError("OEM credential must be a regular file owned by the service user")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise InputError("OEM credential must not be accessible to group or other users")
        chunks: list[bytes] = []
        remaining = MAX_OEM_CREDENTIAL_SIZE + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if not raw or len(raw) > MAX_OEM_CREDENTIAL_SIZE:
        raise InputError("OEM credential is empty or oversized")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InputError("OEM credential is not strict UTF-8 JSON") from error
    if not isinstance(payload, dict) or set(payload) != OEM_CREDENTIAL_KEYS:
        raise InputError("OEM credential has unexpected or missing fields")
    if payload.get("schema") != OEM_CREDENTIAL_SCHEMA:
        raise InputError("OEM credential schema is unsupported")
    return {
        "display_name": validate_display_name(
            require_credential_text("display_name", payload["display_name"])
        ),
        "hostname": validate_hostname(require_credential_text("hostname", payload["hostname"])),
        "password": validate_password(require_credential_text("password", payload["password"])),
        "locale": validate_region_credential_value("locale", payload["locale"]),
        "keymap": validate_region_credential_value("keymap", payload["keymap"]),
        "timezone": validate_region_credential_value("timezone", payload["timezone"]),
    }


def prompt_validated(label: str, validator, default: str | None = None) -> str:
    for _attempt in range(MAX_ATTEMPTS):
        suffix = f" [{default}]" if default else ""
        raw = input(f"{label}{suffix}: ")
        if not raw and default is not None:
            raw = default
        try:
            return validator(raw)
        except InputError as error:
            print(f"Invalid value: {error}", file=sys.stderr)
    raise RuntimeError(f"too many invalid attempts for {label}")


def prompt_password() -> str:
    for _attempt in range(MAX_ATTEMPTS):
        first = getpass.getpass("Local administrator password: ")
        second = getpass.getpass("Confirm local administrator password: ")
        if first != second:
            print("Passwords do not match", file=sys.stderr)
            continue
        try:
            return validate_password(first)
        except InputError as error:
            print(f"Invalid password: {error}", file=sys.stderr)
    raise RuntimeError("too many invalid password attempts")


def run_checked(command: list[str], *, stdin: str | None = None) -> None:
    subprocess.run(
        command,
        input=stdin,
        text=True,
        check=True,
    )


def emit_audit_marker(message: str) -> None:
    """Keep tty interaction visible while mirroring only a non-secret marker."""
    print(message)
    with contextlib.suppress(OSError):
        subprocess.run(
            ["/usr/bin/systemd-cat", "--identifier=echo-oem-setup", "--priority=info"],
            input=message + "\n",
            text=True,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Provisioning is already complete. A logging failure is observable to
        # the boot gate but must not roll back a valid password transaction.


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Echo local-account management must run as root")


def account_record():
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError as error:
        raise RuntimeError(f"required local account is missing: {ACCOUNT}") from error
    if account.pw_uid != 1000 or account.pw_dir != f"/home/{ACCOUNT}":
        raise RuntimeError("local account identity does not match the image contract")
    return account


def atomic_private_write(path: Path, content: str) -> None:
    STATE_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIRECTORY, 0o700)
    temporary = STATE_DIRECTORY / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(STATE_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def write_complete_marker(
    display_name: str,
    hostname: str,
    *,
    completed_unix: int | None = None,
    root_version: str | None = None,
) -> None:
    payload = {
        "schema": 2,
        "account": ACCOUNT,
        "display_name": display_name,
        "hostname": hostname,
        "completed_unix": completed_unix if completed_unix is not None else int(time.time()),
        "root_version": root_version if root_version is not None else current_image_version(),
    }
    atomic_private_write(
        COMPLETE_MARKER,
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
    )


def require_private_root_file(path: Path) -> None:
    try:
        metadata = path.stat()
    except OSError as error:
        raise RuntimeError(f"persistent state is missing: {path}") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
        raise RuntimeError(f"persistent state must be a regular root-owned file: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError(f"persistent state mode must be 0600: {path}")


def read_complete_marker() -> dict[str, object]:
    require_private_root_file(COMPLETE_MARKER)
    try:
        payload = json.loads(COMPLETE_MARKER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("OEM completion state is missing or corrupt") from error
    if payload.get("schema") != 2 or payload.get("account") != ACCOUNT:
        raise RuntimeError("OEM completion state has an unsupported identity or schema")
    payload["display_name"] = validate_display_name(str(payload.get("display_name", "")))
    payload["hostname"] = validate_hostname(str(payload.get("hostname", "")))
    completed_unix = payload.get("completed_unix")
    if not isinstance(completed_unix, int) or completed_unix <= 0:
        raise RuntimeError("OEM completion timestamp is invalid")
    root_version = str(payload.get("root_version", ""))
    if not IMAGE_VERSION_PATTERN.fullmatch(root_version):
        raise RuntimeError("OEM root-version state is invalid")
    payload["root_version"] = root_version
    return payload


def shadow_entry() -> str:
    try:
        with Path("/etc/shadow").open(encoding="utf-8") as stream:
            for line in stream:
                fields = line.rstrip("\n").split(":")
                if fields[0] == ACCOUNT and len(fields) >= 2:
                    return fields[1]
    except OSError as error:
        raise RuntimeError("unable to read the local password database") from error
    raise RuntimeError(f"local password entry is missing: {ACCOUNT}")


def validate_password_hash(value: str) -> str:
    if not 20 <= len(value) <= 512:
        raise RuntimeError("local password hash has an invalid length")
    if value.startswith(("!", "*")) or not PASSWORD_HASH_PATTERN.fullmatch(value):
        raise RuntimeError("local password remains locked or has an invalid hash")
    return value


def current_image_version() -> str:
    try:
        with Path("/usr/lib/os-release").open(encoding="utf-8") as stream:
            values = dict(
                line.rstrip("\n").split("=", 1)
                for line in stream
                if "=" in line and not line.startswith("#")
            )
    except OSError as error:
        raise RuntimeError("unable to read the current image version") from error
    value = values.get("IMAGE_VERSION", "").strip('"')
    if not IMAGE_VERSION_PATTERN.fullmatch(value):
        raise RuntimeError("current image version is missing or invalid")
    return value


def write_shadow_state(password_hash: str) -> None:
    atomic_private_write(SHADOW_STATE, validate_password_hash(password_hash) + "\n")


def read_shadow_state() -> str:
    require_private_root_file(SHADOW_STATE)
    try:
        value = SHADOW_STATE.read_text(encoding="utf-8").rstrip("\n")
    except OSError as error:
        raise RuntimeError("persistent local-account secret is missing") from error
    return validate_password_hash(value)


def capture_account_state() -> int:
    require_root()
    account = account_record()
    marker = read_complete_marker()
    password_hash = validate_password_hash(shadow_entry())
    display_name = validate_display_name(account.pw_gecos.split(",", 1)[0])
    hostname = validate_hostname(socket.gethostname().split(".", 1)[0])
    write_shadow_state(password_hash)
    write_complete_marker(
        display_name,
        hostname,
        completed_unix=int(marker["completed_unix"]),
        root_version=current_image_version(),
    )
    print("Echo OS persistent local-account state captured")
    print(f"ECHO_ACCOUNT_STATE_READY account={ACCOUNT} source=active-root")
    return 0


def restore_account_state() -> int:
    require_root()
    account_record()
    marker = read_complete_marker()
    current_hash = shadow_entry()
    if not current_hash.startswith(("!", "*")):
        return capture_account_state()

    current_version = current_image_version()
    if marker["root_version"] == current_version:
        raise RuntimeError("local administrator is intentionally locked on the current root")

    password_hash = read_shadow_state()
    display_name = str(marker["display_name"])
    hostname = str(marker["hostname"])
    run_checked(["/usr/sbin/usermod", "--comment", display_name, ACCOUNT])
    run_checked(["/usr/sbin/usermod", "--append", "--groups", "sudo", ACCOUNT])
    run_checked(["/usr/bin/hostnamectl", "set-hostname", hostname])
    run_checked(["/usr/sbin/chpasswd", "--encrypted"], stdin=f"{ACCOUNT}:{password_hash}\n")
    if validate_password_hash(shadow_entry()) != password_hash:
        raise RuntimeError("restored password hash does not match persistent account state")
    capture_account_state()
    print(f"ECHO_ACCOUNT_RESTORED account={ACCOUNT} source=persistent-var")
    return 0


def setup_main() -> int:
    try:
        require_root()
    except PermissionError as error:
        print(str(error), file=sys.stderr)
        return 1
    if COMPLETE_MARKER.exists():
        print("Echo OS OEM setup is already complete")
        return 0
    try:
        account_record()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1

    try:
        credential = read_oem_credential()
    except (InputError, OSError) as error:
        print(f"OEM system credential rejected: {error}", file=sys.stderr)
        return 1

    print("\nEcho OS first boot")
    print("Create the password for this device. Echo/Agent cloud login remains separate.\n")
    if credential is None:
        display_name = prompt_validated("Display name", validate_display_name, "Echo User")
        current_hostname = validate_hostname(socket.gethostname().split(".", 1)[0])
        hostname = prompt_validated("Device name", validate_hostname, current_hostname)
        password = prompt_password()
        region_command = ["/usr/bin/python3", str(REGION_STATE_TOOL), "--configure"]
        provision_source = "interactive-tty"
    else:
        display_name = credential["display_name"]
        hostname = credential["hostname"]
        password = credential.pop("password")
        region_command = [
            "/usr/bin/python3",
            str(REGION_STATE_TOOL),
            "--configure-values",
            credential["locale"],
            credential["keymap"],
            credential["timezone"],
        ]
        provision_source = "system-credential"

    try:
        run_checked(region_command)
        run_checked(["/usr/sbin/usermod", "--comment", display_name, ACCOUNT])
        run_checked(["/usr/sbin/usermod", "--append", "--groups", "sudo", ACCOUNT])
        run_checked(["/usr/bin/hostnamectl", "set-hostname", hostname])
        run_checked(["/usr/sbin/chpasswd"], stdin=f"{ACCOUNT}:{password}\n")
        password = ""
        write_shadow_state(validate_password_hash(shadow_entry()))
        write_complete_marker(display_name, hostname)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        password = ""
        print(f"OEM setup failed safely before completion: {error}", file=sys.stderr)
        return 1

    print("\nEcho OS local administrator is ready. The graphical login screen will start now.")
    marker = f"ECHO_OEM_PROVISIONED account={ACCOUNT} source={provision_source}"
    if credential is not None:
        marker += (
            f" locale={credential['locale']} keymap={credential['keymap']}"
            f" timezone={credential['timezone']}"
        )
    emit_audit_marker(marker)
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return setup_main()
    if arguments == ["--capture"]:
        try:
            return capture_account_state()
        except (
            InputError,
            OSError,
            PermissionError,
            RuntimeError,
            subprocess.CalledProcessError,
        ) as error:
            print(f"local-account capture failed: {error}", file=sys.stderr)
            return 1
    if arguments == ["--restore"]:
        try:
            return restore_account_state()
        except (
            InputError,
            OSError,
            PermissionError,
            RuntimeError,
            subprocess.CalledProcessError,
        ) as error:
            print(f"local-account restore failed: {error}", file=sys.stderr)
            return 1
    print("usage: echo-oem-setup [--capture|--restore]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
