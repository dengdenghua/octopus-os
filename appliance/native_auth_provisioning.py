"""Provision native NAS web authentication from the one-time OEM secret."""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
from pathlib import Path
from typing import Any

if os.name == "posix":
    import pwd
else:  # pragma: no cover - production provisioning is Linux-only
    pwd = None  # type: ignore[assignment]

from appliance.auth import (
    ACCOUNT_SESSION_NOT_BEFORE_KEY,
    ACCOUNTS_KEY,
    ADMIN_USERNAME,
    SESSION_NOT_BEFORE_KEY,
    normalized_accounts,
    payload_with_normalized_accounts,
    read_auth_store,
    write_auth_store,
)
from appliance.state_schema import AUTH_SCHEMA_VERSION_KEY, CURRENT_SCHEMA_VERSION

NATIVE_AGENT_STATE_DIRECTORY = Path("/var/lib/echo-agent")


def _validated_password(password: str) -> str:
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("native NAS password must contain 12 to 256 characters")
    if len(password.encode("utf-8")) > 72:
        raise ValueError("native NAS password must be at most 72 UTF-8 bytes")
    if any(ord(character) < 32 or ord(character) == 127 for character in password):
        raise ValueError("native NAS password contains a control character")
    return password


def _existing_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = payload_with_normalized_accounts(read_auth_store(path))
    if payload.get(AUTH_SCHEMA_VERSION_KEY) != CURRENT_SCHEMA_VERSION:
        raise ValueError("native NAS auth store is not at the current schema")
    return payload


def _auth_payload(
    password_hash: str,
    existing: dict[str, Any] | None,
    *,
    jwt_secret: str | None = None,
) -> dict[str, Any]:
    if existing is None:
        payload: dict[str, Any] = {
            "username": ADMIN_USERNAME,
            "password_hash": password_hash,
            "jwt_secret": jwt_secret or secrets.token_urlsafe(48),
            SESSION_NOT_BEFORE_KEY: 0,
            ACCOUNT_SESSION_NOT_BEFORE_KEY: {},
            AUTH_SCHEMA_VERSION_KEY: CURRENT_SCHEMA_VERSION,
        }
        payload[ACCOUNTS_KEY] = normalized_accounts(payload)
        return payload

    payload = dict(existing)
    accounts = normalized_accounts(payload)
    payload["password_hash"] = password_hash
    accounts[ADMIN_USERNAME] = {
        **accounts[ADMIN_USERNAME],
        "password_hash": password_hash,
    }
    payload[ACCOUNTS_KEY] = accounts
    return payload


def provision_native_auth(
    password: str,
    *,
    state_directory: Path = NATIVE_AGENT_STATE_DIRECTORY,
    owner_uid: int,
    owner_gid: int,
) -> Path:
    """Atomically bind the OEM password to the private native NAS auth store."""

    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise PermissionError("native NAS authentication provisioning requires root")
    secret = _validated_password(password)
    if not state_directory.is_absolute() or state_directory.is_symlink():
        raise ValueError("native NAS state directory must be an absolute non-symlink path")
    if owner_uid < 1 or owner_gid < 1:
        raise ValueError("native NAS state owner must be an unprivileged account")

    state_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory_info = state_directory.stat(follow_symlinks=False)
    if not stat.S_ISDIR(directory_info.st_mode):
        raise ValueError("native NAS state path is not a directory")
    state_directory.chmod(0o700)

    from appliance.agent_api.auth import hash_password, verify_password

    path = state_directory / "appliance-auth.json"
    existing = _existing_payload(path)
    password_hash = hash_password(secret)
    payload = _auth_payload(password_hash, existing)

    write_auth_store(payload, path)
    os.chown(path, owner_uid, owner_gid, follow_symlinks=False)
    path.chmod(0o600, follow_symlinks=False)
    os.chown(state_directory, owner_uid, owner_gid, follow_symlinks=False)
    state_directory.chmod(0o700, follow_symlinks=False)
    with contextlib.suppress(OSError):
        directory = os.open(state_directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    stored = read_auth_store(path)
    info = path.stat(follow_symlinks=False)
    parent_info = state_directory.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != owner_uid
        or info.st_gid != owner_gid
        or stat.S_IMODE(parent_info.st_mode) != 0o700
        or parent_info.st_uid != owner_uid
        or parent_info.st_gid != owner_gid
        or not verify_password(secret, str(stored.get("password_hash") or ""))
    ):
        raise RuntimeError("native NAS authentication provisioning did not verify")
    return path


def provision_native_auth_for_account(
    password: str,
    *,
    account: str = "echo",
    state_directory: Path = NATIVE_AGENT_STATE_DIRECTORY,
) -> Path:
    if not account or account != "echo":
        raise ValueError("native NAS authentication owner is not allowed")
    if pwd is None:
        raise PermissionError("native NAS authentication provisioning requires Linux")
    try:
        record = pwd.getpwnam(account)
    except KeyError as exc:
        raise ValueError("native NAS authentication owner does not exist") from exc
    return provision_native_auth(
        password,
        state_directory=state_directory,
        owner_uid=record.pw_uid,
        owner_gid=record.pw_gid,
    )


__all__ = [
    "NATIVE_AGENT_STATE_DIRECTORY",
    "provision_native_auth",
    "provision_native_auth_for_account",
]
