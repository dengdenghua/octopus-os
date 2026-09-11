"""Contracts for binding OEM setup to native NAS web authentication."""

from __future__ import annotations

import pytest

from appliance.auth import ACCOUNTS_KEY, ADMIN_USERNAME, normalized_accounts
from appliance.native_auth_provisioning import _auth_payload, _validated_password
from appliance.state_schema import AUTH_SCHEMA_VERSION_KEY, CURRENT_SCHEMA_VERSION


def test_native_auth_password_matches_the_bcrypt_byte_boundary() -> None:
    assert _validated_password("correct horse 电池 42") == "correct horse 电池 42"
    for value in ("short", "电" * 25, "valid secret\nwith control"):
        with pytest.raises(ValueError):
            _validated_password(value)


def test_fresh_native_auth_payload_contains_no_plaintext() -> None:
    password_hash = "$2b$12$" + "x" * 53
    payload = _auth_payload(password_hash, None, jwt_secret="j" * 64)

    assert payload == {
        "username": ADMIN_USERNAME,
        "password_hash": password_hash,
        "jwt_secret": "j" * 64,
        "session_not_before": 0,
        "account_session_not_before": {},
        AUTH_SCHEMA_VERSION_KEY: CURRENT_SCHEMA_VERSION,
        ACCOUNTS_KEY: {
            ADMIN_USERNAME: {
                "display_name": "管理员",
                "role": "admin",
                "password_hash": password_hash,
                "omv_username": None,
                "active": True,
            }
        },
    }
    assert "correct horse" not in repr(payload)
    assert normalized_accounts(payload) == payload[ACCOUNTS_KEY]


def test_retry_rotates_only_admin_hash_and_preserves_device_identity() -> None:
    old_hash = "$2b$12$" + "o" * 53
    new_hash = "$2b$12$" + "n" * 53
    existing = _auth_payload(old_hash, None, jwt_secret="s" * 64)
    existing[ACCOUNTS_KEY]["alice"] = {
        "display_name": "Alice",
        "role": "member",
        "password_hash": "$2b$12$" + "a" * 53,
        "omv_username": "alice",
        "active": True,
    }

    updated = _auth_payload(new_hash, existing)

    assert updated["jwt_secret"] == existing["jwt_secret"]
    assert updated["password_hash"] == new_hash
    assert updated[ACCOUNTS_KEY][ADMIN_USERNAME]["password_hash"] == new_hash
    assert updated[ACCOUNTS_KEY]["alice"] == existing[ACCOUNTS_KEY]["alice"]
