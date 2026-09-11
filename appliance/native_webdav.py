"""Authenticated WebDAV-to-SFTP identity bridge for the native appliance.

The public WebDAV endpoint is terminated by the appliance nginx instance and
proxied to rclone on loopback.  Rclone calls this module for every Basic-auth
identity and receives a backend rooted in a per-POSIX-user OpenSSH chroot.

The bridge deliberately does not pass the Echo password to OpenSSH: linked
family accounts are allowed to use a different Echo password from their NAS
account password.  A per-device gateway key authenticates the already-checked
identity to a dedicated loopback-only sshd instead.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, BinaryIO

from appliance.agent_api.passwords import verify_password
from appliance.auth import ADMIN_USERNAME, normalized_accounts, read_auth_store

MANIFEST_SCHEMA = "echo.webdav-jails.v1"
MANIFEST_PATH = Path("/run/echo-webdav/jails.json")
KNOWN_HOSTS_PATH = Path("/var/lib/echo-os/webdav-gateway/known_hosts")
SFTP_HOST = "127.0.0.1"
SFTP_PORT = 22022
SFTP_ROOT = "/shares"
MAX_REQUEST_BYTES = 16 * 1024
MAX_PASSWORD_BYTES = 72
_USERNAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")


class WebDavAuthError(ValueError):
    """A fail-closed authentication or runtime-contract rejection."""


def _safe_regular_file(path: Path, *, private: bool = False) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise WebDavAuthError("required WebDAV state is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise WebDavAuthError("required WebDAV state is unsafe")
    if os.name == "posix":
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o022:
            raise WebDavAuthError("required WebDAV state is writable by group or others")
        if private and mode & 0o077:
            raise WebDavAuthError("WebDAV gateway credential is not private")
        if info.st_uid not in {0, os.getuid()}:
            raise WebDavAuthError("required WebDAV state has an unexpected owner")


def _read_manifest(path: Path) -> dict[str, dict[str, Any]]:
    _safe_regular_file(path)
    try:
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise WebDavAuthError("WebDAV jail manifest is too large")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WebDavAuthError("WebDAV jail manifest is unreadable") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "accounts"}:
        raise WebDavAuthError("WebDAV jail manifest has an invalid shape")
    if payload.get("schema") != MANIFEST_SCHEMA or not isinstance(payload.get("accounts"), dict):
        raise WebDavAuthError("WebDAV jail manifest has an invalid identity")

    accounts: dict[str, dict[str, Any]] = {}
    for login, record in payload["accounts"].items():
        if (
            not isinstance(login, str)
            or _USERNAME.fullmatch(login) is None
            or not isinstance(record, dict)
            or set(record) != {"posixUser", "shares"}
        ):
            raise WebDavAuthError("WebDAV jail manifest contains an invalid account")
        posix_user = record.get("posixUser")
        shares = record.get("shares")
        if (
            not isinstance(posix_user, str)
            or _USERNAME.fullmatch(posix_user) is None
            or not isinstance(shares, list)
            or not shares
            or len(shares) > 256
            or any(
                not isinstance(item, dict)
                or set(item) != {"name", "uuid"}
                or not isinstance(item.get("name"), str)
                or not item["name"]
                or len(item["name"]) > 128
                or not isinstance(item.get("uuid"), str)
                or len(item["uuid"]) != 36
                for item in shares
            )
        ):
            raise WebDavAuthError("WebDAV jail manifest contains an invalid mapping")
        accounts[login] = {"posixUser": posix_user, "shares": list(shares)}
    return accounts


def _parse_request(stream: BinaryIO) -> tuple[str, str]:
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise WebDavAuthError("WebDAV authentication request is too large")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WebDavAuthError("WebDAV authentication request is invalid") from exc
    if not isinstance(payload, dict) or not {"user", "pass"}.issubset(payload):
        raise WebDavAuthError("WebDAV authentication request is incomplete")
    if set(payload) - {"user", "pass", "client_ip"}:
        raise WebDavAuthError("WebDAV authentication request has unexpected fields")
    username = payload.get("user")
    password = payload.get("pass")
    client_ip = payload.get("client_ip")
    if (
        not isinstance(username, str)
        or _USERNAME.fullmatch(username) is None
        or not isinstance(password, str)
        or not password
        or len(password.encode("utf-8")) > MAX_PASSWORD_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in password)
    ):
        raise WebDavAuthError("WebDAV credentials are invalid")
    if client_ip is not None:
        if not isinstance(client_ip, str) or len(client_ip) > 64:
            raise WebDavAuthError("WebDAV client address is invalid")
        try:
            ipaddress.ip_address(client_ip)
        except ValueError as exc:
            raise WebDavAuthError("WebDAV client address is invalid") from exc
    return username, password


def authenticate(
    username: str,
    password: str,
    *,
    manifest_path: Path = MANIFEST_PATH,
    auth_path: Path | None = None,
    credential_directory: Path | None = None,
    known_hosts_path: Path | None = None,
    validate_runtime_files: bool = True,
) -> dict[str, Any]:
    """Validate one Echo login and return a complete pinned SFTP backend."""

    try:
        payload = read_auth_store(auth_path)
        accounts = normalized_accounts(payload)
    except (OSError, ValueError) as exc:
        raise WebDavAuthError("WebDAV account directory is unavailable") from exc

    account = accounts.get(username)
    # Always perform one password verification for a syntactically valid login;
    # unknown names use the administrator hash to avoid a cheap timing oracle.
    candidate_hash = str((account or accounts[ADMIN_USERNAME]).get("password_hash") or "")
    password_valid = verify_password(password, candidate_hash)
    if account is None or account.get("active") is not True or not password_valid:
        raise WebDavAuthError("WebDAV credentials were rejected")

    mappings = _read_manifest(manifest_path)
    mapping = mappings.get(username)
    expected_posix = "echo" if username == ADMIN_USERNAME else account.get("omv_username")
    if (
        mapping is None
        or not isinstance(expected_posix, str)
        or mapping.get("posixUser") != expected_posix
    ):
        raise WebDavAuthError("WebDAV share mapping is unavailable")

    credentials = credential_directory
    if credentials is None:
        raw_directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
        credentials = Path(raw_directory) if raw_directory else None
    if credentials is None or not credentials.is_absolute():
        raise WebDavAuthError("WebDAV gateway credential directory is unavailable")
    private_key = credentials / "webdav-sftp.key"
    pinned_hosts = known_hosts_path or credentials / "webdav-known-hosts"
    if validate_runtime_files:
        # systemd exposes credential copies from a unit-private, read-only
        # directory; their file mode may be 0444 on newer releases. The
        # root-owned source key remains 0400 and is never opened by rclone.
        _safe_regular_file(private_key)
        _safe_regular_file(pinned_hosts)

    return {
        "type": "sftp",
        "_root": SFTP_ROOT,
        "host": SFTP_HOST,
        "port": str(SFTP_PORT),
        "user": expected_posix,
        "key_file": str(private_key),
        "known_hosts_file": str(pinned_hosts),
        "shell_type": "none",
        "disable_hashcheck": "true",
        "set_modtime": "true",
    }


def run_auth_proxy(
    stream: BinaryIO | None = None,
    output: BinaryIO | None = None,
) -> int:
    """Implement rclone's one-request JSON auth-proxy protocol."""

    source = stream or sys.stdin.buffer
    target = output or sys.stdout.buffer
    try:
        username, password = _parse_request(source)
        backend = authenticate(username, password)
    except WebDavAuthError as exc:
        # rclone treats an empty/non-zero response as authentication failure.
        # The privileged journal gets a reason without any supplied credential.
        print(f"Echo WebDAV authentication rejected: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("Echo WebDAV authentication failed unexpectedly", file=sys.stderr)
        return 1
    target.write(json.dumps(backend, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    target.write(b"\n")
    target.flush()
    return 0


def main() -> int:
    return run_auth_proxy()


if __name__ == "__main__":  # pragma: no cover - exercised by service smoke tests
    raise SystemExit(main())


__all__ = [
    "KNOWN_HOSTS_PATH",
    "MANIFEST_PATH",
    "MANIFEST_SCHEMA",
    "SFTP_HOST",
    "SFTP_PORT",
    "SFTP_ROOT",
    "WebDavAuthError",
    "authenticate",
    "run_auth_proxy",
]
