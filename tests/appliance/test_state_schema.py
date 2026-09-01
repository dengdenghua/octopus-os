"""Persistent state has explicit forward migration and downgrade guards."""

from __future__ import annotations

import json
import stat

import pytest

from appliance.state_schema import (
    AUTH_SCHEMA_VERSION_KEY,
    CURRENT_SCHEMA_VERSION,
    STATE_SCHEMA_FILENAME,
    STATE_SCHEMA_KIND,
    StateSchemaError,
    ensure_state_schema,
    inspect_state_schema,
)


def test_legacy_directory_runs_explicit_v0_to_v1_to_v2_without_touching_data(tmp_path) -> None:
    existing = tmp_path / "agent-memory.json"
    existing.write_text('{"memory":"preserved"}')

    before = inspect_state_schema(tmp_path)
    migrated = ensure_state_schema(tmp_path)
    repeated = ensure_state_schema(tmp_path)

    marker = tmp_path / STATE_SCHEMA_FILENAME
    payload = json.loads(marker.read_text())
    assert before["version"] == 0 and before["migrationRequired"] is True
    assert migrated["version"] == CURRENT_SCHEMA_VERSION
    assert migrated["migratedFrom"] == 0
    assert repeated["migratedFrom"] is None
    assert payload["kind"] == STATE_SCHEMA_KIND
    assert payload["version"] == CURRENT_SCHEMA_VERSION
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert existing.read_text() == '{"memory":"preserved"}'


def test_v1_directory_advances_exactly_once_to_audit_keyring_schema(tmp_path) -> None:
    marker = tmp_path / STATE_SCHEMA_FILENAME
    marker.write_text(
        json.dumps(
            {
                "kind": STATE_SCHEMA_KIND,
                "version": 1,
                "minimumCompatibleVersion": 0,
            }
        )
    )

    migrated = ensure_state_schema(tmp_path)
    repeated = ensure_state_schema(tmp_path)

    assert migrated["version"] == 2
    assert migrated["migratedFrom"] == 1
    assert repeated["migratedFrom"] is None
    assert not (tmp_path / "appliance-audit-keyring.json").exists()


def test_newer_state_refuses_unsafe_downgrade_without_mutation(tmp_path) -> None:
    marker = tmp_path / STATE_SCHEMA_FILENAME
    marker.write_text(
        json.dumps(
            {
                "kind": STATE_SCHEMA_KIND,
                "version": CURRENT_SCHEMA_VERSION + 1,
                "minimumCompatibleVersion": CURRENT_SCHEMA_VERSION,
            }
        )
    )
    original = marker.read_bytes()

    report = inspect_state_schema(tmp_path, require_compatible=False)
    assert report["compatible"] is False
    with pytest.raises(StateSchemaError, match="newer Echo version"):
        ensure_state_schema(tmp_path)
    assert marker.read_bytes() == original


def test_invalid_or_symlinked_schema_marker_fails_closed(tmp_path) -> None:
    marker = tmp_path / STATE_SCHEMA_FILENAME
    marker.write_text('{"kind":"wrong","version":1}')
    with pytest.raises(StateSchemaError, match="marker is invalid"):
        inspect_state_schema(tmp_path)

    marker.unlink()
    outside = tmp_path.parent / f"{tmp_path.name}-outside-schema"
    outside.write_text(
        json.dumps(
            {
                "kind": STATE_SCHEMA_KIND,
                "version": CURRENT_SCHEMA_VERSION,
                "minimumCompatibleVersion": 0,
            }
        )
    )
    marker.symlink_to(outside)
    with pytest.raises(StateSchemaError, match="must not be a symlink"):
        inspect_state_schema(tmp_path)


def test_auth_anchor_recovers_missing_marker_and_blocks_future_version(tmp_path) -> None:
    auth = tmp_path / "appliance-auth.json"
    auth.write_text('{"username":"admin","session_not_before":0}')

    ensure_state_schema(tmp_path)
    anchored = json.loads(auth.read_text())
    assert anchored[AUTH_SCHEMA_VERSION_KEY] == CURRENT_SCHEMA_VERSION

    marker = tmp_path / STATE_SCHEMA_FILENAME
    marker.unlink()
    recovered = ensure_state_schema(tmp_path)
    assert recovered["version"] == CURRENT_SCHEMA_VERSION
    assert marker.is_file()

    marker.unlink()
    anchored[AUTH_SCHEMA_VERSION_KEY] = CURRENT_SCHEMA_VERSION + 1
    auth.write_text(json.dumps(anchored))
    with pytest.raises(StateSchemaError, match="newer Echo version"):
        ensure_state_schema(tmp_path)
    assert not marker.exists()
