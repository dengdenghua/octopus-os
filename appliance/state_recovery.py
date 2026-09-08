"""Offline validation for a restored Echo appliance state directory.

This is intentionally read-only.  The host recovery orchestrator decrypts a
backup into a new directory, advances only explicitly supported schema
migrations on that staging copy, then calls this module before any directory
promotion.  The live state is never merged with restored content.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from appliance.audit import (
    AUDIT_FILENAME,
    AUDIT_KEYRING_FILENAME,
    MAX_KEYRING_BYTES,
    ApplianceAudit,
    AuditIntegrityError,
)
from appliance.auth import (
    ACCOUNT_SESSION_NOT_BEFORE_KEY,
    ADMIN_USERNAME,
    SESSION_NOT_BEFORE_KEY,
    normalized_accounts,
    read_auth_store,
)
from appliance.nas_alert_delivery import (
    ALERT_DELIVERY_FILENAME,
    MAX_ALERT_DELIVERY_BYTES,
    validate_alert_delivery_state,
)
from appliance.nas_email_alert_delivery import (
    EMAIL_ALERT_DELIVERY_FILENAME,
    MAX_EMAIL_ALERT_DELIVERY_BYTES,
    validate_email_alert_delivery_state,
)
from appliance.state_lock import LOCK_FILENAME
from appliance.state_schema import (
    AUTH_SCHEMA_VERSION_KEY,
    CURRENT_SCHEMA_VERSION,
    MAX_SCHEMA_MARKER_BYTES,
    STATE_SCHEMA_FILENAME,
    StateSchemaError,
    inspect_state_schema,
)
from appliance.totp import MAX_TOTP_STATE_BYTES, TOTP_FILENAME, validate_totp_state

_BCRYPT = re.compile(r"^bcrypt:\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")
_LEGACY_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
MAX_AUTH_BYTES = 64 * 1024
MAX_AUDIT_BYTES = 1024 * 1024 * 1024
MAX_CHECKPOINT_BYTES = 64 * 1024
OWNER_MARKER = ".echo-runtime-owner"


class StateRecoveryError(RuntimeError):
    """A restored directory cannot safely become the active device state."""


def _private_regular_file(path: Path, *, maximum_bytes: int) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise StateRecoveryError(f"required restored state file is missing: {path.name}") from exc
    if os.name == "nt":
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_size < 1
            or info.st_size > maximum_bytes
        ):
            raise StateRecoveryError(f"restored state file is unsafe: {path.name}")
        try:
            from appliance.windows_state import open_private_file, private_state_directory

            with private_state_directory(path.parent, protect=True):
                descriptor = open_private_file(path)
                os.close(descriptor)
        except (OSError, ValueError) as exc:
            raise StateRecoveryError(f"restored state file is unsafe: {path.name}") from exc
        return
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_size < 1
        or info.st_size > maximum_bytes
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise StateRecoveryError(f"restored state file is unsafe: {path.name}")


def _validate_password_hash(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise StateRecoveryError("restored authentication password hash is missing")
    if _BCRYPT.fullmatch(value) is None and _LEGACY_SHA256.fullmatch(value) is None:
        raise StateRecoveryError("restored authentication password hash is invalid")
    return "bcrypt" if value.startswith("bcrypt:") else "legacy-sha256"


def inspect_restored_state(
    state_dir: Path | str,
    *,
    require_current: bool = True,
) -> dict[str, Any]:
    root = Path(state_dir)
    try:
        info = root.lstat()
    except FileNotFoundError as exc:
        raise StateRecoveryError(f"restored state directory does not exist: {root}") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise StateRecoveryError(f"restored state directory is unsafe: {root}")
    if os.name == "nt":
        try:
            from appliance.windows_state import private_state_directory

            with private_state_directory(root, protect=True):
                pass
        except (OSError, ValueError) as exc:
            raise StateRecoveryError(f"restored state directory is unsafe: {root}") from exc
    if (root / "nas").exists() or (root / "nas").is_symlink():
        raise StateRecoveryError("restored state unexpectedly contains NAS user data")
    if (root / LOCK_FILENAME).exists() or (root / LOCK_FILENAME).is_symlink():
        raise StateRecoveryError("restored state unexpectedly contains a runtime lock")

    optional_private_files = {
        AUDIT_FILENAME: MAX_AUDIT_BYTES,
        f"{AUDIT_FILENAME}.checkpoint": MAX_CHECKPOINT_BYTES,
        AUDIT_KEYRING_FILENAME: MAX_KEYRING_BYTES,
        OWNER_MARKER: 1024,
        TOTP_FILENAME: MAX_TOTP_STATE_BYTES,
        ALERT_DELIVERY_FILENAME: MAX_ALERT_DELIVERY_BYTES,
        EMAIL_ALERT_DELIVERY_FILENAME: MAX_EMAIL_ALERT_DELIVERY_BYTES,
    }
    for name, maximum_bytes in optional_private_files.items():
        path = root / name
        if path.exists() or path.is_symlink():
            _private_regular_file(path, maximum_bytes=maximum_bytes)

    try:
        schema = inspect_state_schema(root)
    except StateSchemaError as exc:
        raise StateRecoveryError(str(exc)) from exc
    if require_current and (
        schema["version"] != CURRENT_SCHEMA_VERSION or schema["migrationRequired"] is not False
    ):
        raise StateRecoveryError(
            "restored state must complete supported migrations before promotion"
        )
    if require_current:
        _private_regular_file(
            root / STATE_SCHEMA_FILENAME,
            maximum_bytes=MAX_SCHEMA_MARKER_BYTES,
        )

    auth_path = root / "appliance-auth.json"
    _private_regular_file(auth_path, maximum_bytes=MAX_AUTH_BYTES)
    try:
        auth = read_auth_store(auth_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise StateRecoveryError("restored authentication store is unreadable") from exc
    if auth.get("username") != ADMIN_USERNAME:
        raise StateRecoveryError("restored authentication store has the wrong administrator")
    hash_kind = _validate_password_hash(auth.get("password_hash"))
    try:
        accounts = normalized_accounts(auth)
    except ValueError as exc:
        raise StateRecoveryError("restored authentication account directory is invalid") from exc
    for account in accounts.values():
        _validate_password_hash(account["password_hash"])
    jwt_secret = auth.get("jwt_secret")
    if not isinstance(jwt_secret, str) or len(jwt_secret) < 32 or len(jwt_secret) > 4096:
        raise StateRecoveryError("restored authentication signing secret is invalid")
    try:
        from appliance.agent_api.auth import LocalAuthConfig

        LocalAuthConfig(
            enabled=True,
            allow_any_username=False,
            users={ADMIN_USERNAME: str(auth["password_hash"])},
            jwt_secret=jwt_secret,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StateRecoveryError("restored authentication signing secret is invalid") from exc
    session_not_before = auth.get(SESSION_NOT_BEFORE_KEY)
    if (
        isinstance(session_not_before, bool)
        or not isinstance(session_not_before, int)
        or session_not_before < 0
    ):
        raise StateRecoveryError("restored session revocation epoch is invalid")
    account_floors = auth.get(ACCOUNT_SESSION_NOT_BEFORE_KEY, {})
    if not isinstance(account_floors, dict) or any(
        not isinstance(username, str)
        or re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", username) is None
        or isinstance(floor, bool)
        or not isinstance(floor, int)
        or floor < 0
        for username, floor in account_floors.items()
    ):
        raise StateRecoveryError("restored account session revocation epochs are invalid")
    if require_current and auth.get(AUTH_SCHEMA_VERSION_KEY) != CURRENT_SCHEMA_VERSION:
        raise StateRecoveryError("restored authentication schema anchor is not current")

    totp_enabled = False
    recovery_codes_remaining = 0
    totp_path = root / TOTP_FILENAME
    if totp_path.exists() or totp_path.is_symlink():
        try:
            totp_state = validate_totp_state(read_auth_store(totp_path))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise StateRecoveryError("restored administrator TOTP state is invalid") from exc
        totp_enabled = True
        recovery_codes_remaining = len(totp_state["recovery_code_hashes"])

    alert_delivery_configured = False
    alert_delivery_enabled = False
    alert_delivery_path = root / ALERT_DELIVERY_FILENAME
    if alert_delivery_path.exists() or alert_delivery_path.is_symlink():
        try:
            alert_delivery = validate_alert_delivery_state(
                alert_delivery_path,
                encryption_secret=jwt_secret,
            )
        except (OSError, ValueError) as exc:
            raise StateRecoveryError("restored NAS alert delivery state is invalid") from exc
        alert_delivery_configured = alert_delivery["url"] is not None
        alert_delivery_enabled = alert_delivery["enabled"]

    email_alert_delivery_configured = False
    email_alert_delivery_enabled = False
    email_alert_delivery_path = root / EMAIL_ALERT_DELIVERY_FILENAME
    if email_alert_delivery_path.exists() or email_alert_delivery_path.is_symlink():
        try:
            email_alert_delivery = validate_email_alert_delivery_state(
                email_alert_delivery_path,
                encryption_secret=jwt_secret,
            )
        except (OSError, ValueError) as exc:
            raise StateRecoveryError("restored NAS email alert state is invalid") from exc
        email_alert_delivery_configured = email_alert_delivery["smtpHost"] is not None
        email_alert_delivery_enabled = email_alert_delivery["enabled"]

    try:
        audit = ApplianceAudit.from_data_dir(root, jwt_secret=jwt_secret)
        audit_report = audit.verify()
        anchor = audit.anchor()
    except (AuditIntegrityError, OSError, ValueError) as exc:
        raise StateRecoveryError("restored audit trail failed verification") from exc
    if not audit_report.ok:
        raise StateRecoveryError(audit_report.error or "restored audit trail failed verification")

    return {
        "ok": True,
        "stateDir": str(root),
        "schemaVersion": schema["version"],
        "schemaCurrent": schema["version"] == CURRENT_SCHEMA_VERSION,
        "migrationRequired": schema["migrationRequired"],
        "administrator": ADMIN_USERNAME,
        "localAccounts": len(accounts),
        "passwordHashKind": hash_kind,
        "sessionNotBefore": session_not_before,
        "administratorTotpEnabled": totp_enabled,
        "administratorRecoveryCodesRemaining": recovery_codes_remaining,
        "nasAlertDeliveryConfigured": alert_delivery_configured,
        "nasAlertDeliveryEnabled": alert_delivery_enabled,
        "nasEmailAlertDeliveryConfigured": email_alert_delivery_configured,
        "nasEmailAlertDeliveryEnabled": email_alert_delivery_enabled,
        "auditEntries": audit_report.entries_checked,
        "auditSigningKeyId": anchor["signing"]["keyId"],
        "nasUserDataIncluded": False,
        "runtimeLockIncluded": False,
        "readOnlyInspection": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate restored Echo appliance state")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("ECHO_DATA_DIR") or "/data"),
    )
    parser.add_argument(
        "--allow-migration",
        action="store_true",
        help="report compatible older state without requiring current schema",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = inspect_restored_state(
            args.state_dir,
            require_current=not args.allow_migration,
        )
    except StateRecoveryError as exc:
        print(f"Echo state recovery check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["StateRecoveryError", "inspect_restored_state", "main"]
