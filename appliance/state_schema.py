"""Version contract for persistent Echo appliance state.

Version 0 means a legacy directory created before the marker existed. The first
explicit migration (0 -> 1) is metadata-only. Version 2 introduces the durable
audit signing-key ring and externally verifiable anchors. Future state changes must add a
numbered, forward-only migration here before increasing CURRENT_SCHEMA_VERSION.
An older runtime refuses a newer marker instead of guessing or mutating it.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import secrets
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

STATE_SCHEMA_FILENAME = "echo-state-schema.json"
STATE_SCHEMA_KIND = "echo-appliance-state"
AUTH_SCHEMA_VERSION_KEY = "state_schema_version"
LEGACY_SCHEMA_VERSION = 0
CURRENT_SCHEMA_VERSION = 2
MINIMUM_READABLE_SCHEMA_VERSION = 0
MAX_SCHEMA_MARKER_BYTES = 64 * 1024


class StateSchemaError(RuntimeError):
    pass


def _marker_path(state_dir: Path | str) -> Path:
    return Path(state_dir) / STATE_SCHEMA_FILENAME


def inspect_state_schema(
    state_dir: Path | str,
    *,
    require_compatible: bool = True,
) -> dict[str, Any]:
    root = Path(state_dir)
    if root.is_symlink() or not root.is_dir():
        raise StateSchemaError(f"state directory is missing or unsafe: {root}")
    marker = _marker_path(root)
    if marker.is_symlink():
        raise StateSchemaError("state schema marker must not be a symlink")
    marker_present = marker.exists()
    marker_version: int | None = None
    marker_minimum: int | None = None
    if not marker.exists():
        pass
    else:
        try:
            info = marker.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SCHEMA_MARKER_BYTES:
                raise StateSchemaError("state schema marker is not a safe regular file")
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StateSchemaError("state schema marker is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("kind") != STATE_SCHEMA_KIND:
            raise StateSchemaError("state schema marker is invalid")
        marker_version = payload.get("version")
        marker_minimum = payload.get("minimumCompatibleVersion", marker_version)
        if (
            isinstance(marker_version, bool)
            or not isinstance(marker_version, int)
            or marker_version < LEGACY_SCHEMA_VERSION
            or isinstance(marker_minimum, bool)
            or not isinstance(marker_minimum, int)
            or marker_minimum < LEGACY_SCHEMA_VERSION
            or marker_minimum > marker_version
        ):
            raise StateSchemaError("state schema version is invalid")

    auth_path = root / "appliance-auth.json"
    if auth_path.is_symlink():
        raise StateSchemaError("appliance auth store must not be a symlink")
    auth_version: int | None = None
    if auth_path.exists():
        try:
            auth_info = auth_path.stat()
            if not stat.S_ISREG(auth_info.st_mode):
                raise StateSchemaError("appliance auth store is not a regular file")
            auth_payload = json.loads(auth_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StateSchemaError("appliance auth store is unreadable") from exc
        if not isinstance(auth_payload, dict):
            raise StateSchemaError("appliance auth store is invalid")
        auth_version = auth_payload.get(AUTH_SCHEMA_VERSION_KEY)
        if auth_version is not None and (
            isinstance(auth_version, bool)
            or not isinstance(auth_version, int)
            or auth_version < LEGACY_SCHEMA_VERSION
        ):
            raise StateSchemaError("appliance auth schema anchor is invalid")

    if marker_version is not None and auth_version is not None and marker_version != auth_version:
        raise StateSchemaError("state schema markers disagree; refusing to guess")
    version = (
        marker_version
        if marker_version is not None
        else auth_version
        if auth_version is not None
        else LEGACY_SCHEMA_VERSION
    )
    recorded_minimum = marker_minimum if marker_minimum is not None else LEGACY_SCHEMA_VERSION

    compatible = (
        MINIMUM_READABLE_SCHEMA_VERSION <= version <= CURRENT_SCHEMA_VERSION
        and recorded_minimum <= CURRENT_SCHEMA_VERSION
    )
    if require_compatible and not compatible:
        if version > CURRENT_SCHEMA_VERSION:
            raise StateSchemaError(
                "state was written by a newer Echo version; refusing unsafe downgrade"
            )
        raise StateSchemaError("state schema is no longer supported by this Echo version")
    return {
        "kind": STATE_SCHEMA_KIND,
        "version": version,
        "minimumCompatibleVersion": recorded_minimum,
        "currentRuntimeVersion": CURRENT_SCHEMA_VERSION,
        "compatible": compatible,
        "migrationRequired": compatible and version < CURRENT_SCHEMA_VERSION,
        "marker": str(marker),
        "markerPresent": marker_present,
        "authVersionRecorded": auth_version,
    }


def _write_schema_marker(state_dir: Path, version: int) -> None:
    marker = _marker_path(state_dir)
    if marker.is_symlink():
        raise StateSchemaError("state schema marker must not be a symlink")
    payload = {
        "kind": STATE_SCHEMA_KIND,
        "version": version,
        "minimumCompatibleVersion": MINIMUM_READABLE_SCHEMA_VERSION,
        "updatedAt": dt.datetime.now(dt.UTC).isoformat(),
    }
    temporary = marker.with_name(f".{marker.name}.{secrets.token_hex(8)}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except OSError as exc:
        raise StateSchemaError("state schema marker could not be written") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
        marker.chmod(0o600)
        with contextlib.suppress(OSError):
            directory = os.open(state_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as exc:
        raise StateSchemaError("state schema marker could not be written") from exc
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _set_schema_version(state_dir: Path, version: int) -> None:
    auth_path = state_dir / "appliance-auth.json"
    if auth_path.exists():
        from appliance.auth import read_auth_store, write_auth_store

        auth_payload = read_auth_store(auth_path)
        auth_payload[AUTH_SCHEMA_VERSION_KEY] = version
        write_auth_store(auth_payload, auth_path)
    _write_schema_marker(state_dir, version)


def _migrate_v0_to_v1(state_dir: Path) -> None:
    # Introduce the durable marker. No user or Agent data changes.
    _set_schema_version(state_dir, 1)


def _migrate_v1_to_v2(state_dir: Path) -> None:
    # The keyring is created only on the first explicit signing-key rotation.
    # Advancing the marker first ensures older runtimes refuse a state that may
    # later contain non-v1 audit records instead of mis-verifying the chain.
    _set_schema_version(state_dir, 2)


MIGRATIONS: dict[int, Callable[[Path], None]] = {
    LEGACY_SCHEMA_VERSION: _migrate_v0_to_v1,
    1: _migrate_v1_to_v2,
}


def ensure_state_schema(state_dir: Path | str) -> dict[str, Any]:
    """Validate current state and run every explicit one-version migration."""

    root = Path(state_dir)
    status = inspect_state_schema(root)
    original_version = status["version"]
    while status["version"] < CURRENT_SCHEMA_VERSION:
        source_version = status["version"]
        migration = MIGRATIONS.get(source_version)
        if migration is None:
            raise StateSchemaError(f"no migration from state schema {source_version} is available")
        migration(root)
        status = inspect_state_schema(root)
        if status["version"] != source_version + 1:
            raise StateSchemaError("state migration did not advance exactly one version")
    if not status["markerPresent"] or (
        (root / "appliance-auth.json").exists() and status["authVersionRecorded"] is None
    ):
        _set_schema_version(root, status["version"])
        status = inspect_state_schema(root)
    status["migratedFrom"] = original_version if original_version != status["version"] else None
    return status


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect Echo appliance state schema")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("ECHO_DATA_DIR") or "/data"),
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="apply explicitly supported forward migrations",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = (
            ensure_state_schema(args.state_dir)
            if args.prepare
            else inspect_state_schema(args.state_dir)
        )
    except StateSchemaError as exc:
        print(f"Echo state schema check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "AUTH_SCHEMA_VERSION_KEY",
    "LEGACY_SCHEMA_VERSION",
    "MINIMUM_READABLE_SCHEMA_VERSION",
    "STATE_SCHEMA_FILENAME",
    "StateSchemaError",
    "ensure_state_schema",
    "inspect_state_schema",
    "main",
]
