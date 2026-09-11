from __future__ import annotations

import io
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from appliance import native_webdav
from appliance.agent_api.auth import hash_password


def _auth_store(path: Path, *, member_active: bool = True) -> Path:
    admin_hash = hash_password("Admin-Password-123!")
    member_hash = hash_password("Member-Password-123!")
    path.write_text(
        json.dumps(
            {
                "username": "admin",
                "password_hash": admin_hash,
                "jwt_secret": "x" * 48,
                "accounts": {
                    "admin": {
                        "display_name": "Admin",
                        "role": "admin",
                        "password_hash": admin_hash,
                        "omv_username": None,
                        "active": True,
                    },
                    "alice": {
                        "display_name": "Alice",
                        "role": "member",
                        "password_hash": member_hash,
                        "omv_username": "alice",
                        "active": member_active,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _manifest(path: Path, *, include_alice: bool = True) -> Path:
    accounts = {
        "admin": {
            "posixUser": "echo",
            "shares": [{"name": "photos", "uuid": "1" * 36}],
        }
    }
    if include_alice:
        accounts["alice"] = {
            "posixUser": "alice",
            "shares": [{"name": "photos", "uuid": "1" * 36}],
        }
    path.write_text(
        json.dumps({"schema": native_webdav.MANIFEST_SCHEMA, "accounts": accounts}),
        encoding="utf-8",
    )
    return path


def test_authenticate_maps_echo_login_to_posix_identity_without_reusing_password(
    tmp_path: Path,
) -> None:
    backend = native_webdav.authenticate(
        "alice",
        "Member-Password-123!",
        auth_path=_auth_store(tmp_path / "auth.json"),
        manifest_path=_manifest(tmp_path / "jails.json"),
        credential_directory=tmp_path / "credentials",
        known_hosts_path=tmp_path / "known_hosts",
        validate_runtime_files=False,
    )

    assert backend == {
        "type": "sftp",
        "_root": "/shares",
        "host": "127.0.0.1",
        "port": "22022",
        "user": "alice",
        "key_file": str(tmp_path / "credentials" / "webdav-sftp.key"),
        "known_hosts_file": str(tmp_path / "known_hosts"),
        "shell_type": "none",
        "disable_hashcheck": "true",
        "set_modtime": "true",
    }
    assert "pass" not in backend


def test_authenticate_maps_admin_to_echo(tmp_path: Path) -> None:
    backend = native_webdav.authenticate(
        "admin",
        "Admin-Password-123!",
        auth_path=_auth_store(tmp_path / "auth.json"),
        manifest_path=_manifest(tmp_path / "jails.json"),
        credential_directory=tmp_path / "credentials",
        validate_runtime_files=False,
    )
    assert backend["user"] == "echo"


@pytest.mark.parametrize(
    ("username", "password", "active", "mapped"),
    [
        ("alice", "wrong-password", True, True),
        ("missing", "Member-Password-123!", True, True),
        ("alice", "Member-Password-123!", False, True),
        ("alice", "Member-Password-123!", True, False),
    ],
)
def test_authenticate_fails_closed(
    tmp_path: Path,
    username: str,
    password: str,
    active: bool,
    mapped: bool,
) -> None:
    with pytest.raises(native_webdav.WebDavAuthError):
        native_webdav.authenticate(
            username,
            password,
            auth_path=_auth_store(tmp_path / "auth.json", member_active=active),
            manifest_path=_manifest(tmp_path / "jails.json", include_alice=mapped),
            credential_directory=tmp_path / "credentials",
            validate_runtime_files=False,
        )


def test_auth_proxy_rejects_oversize_or_extra_fields_without_output() -> None:
    for payload in (
        b"x" * (native_webdav.MAX_REQUEST_BYTES + 1),
        json.dumps({"user": "admin", "pass": "x", "token": "secret"}).encode(),
    ):
        output = io.BytesIO()
        assert native_webdav.run_auth_proxy(io.BytesIO(payload), output) == 1
        assert output.getvalue() == b""


def test_manifest_rejects_empty_share_mapping(tmp_path: Path) -> None:
    manifest = tmp_path / "jails.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": native_webdav.MANIFEST_SCHEMA,
                "accounts": {"admin": {"posixUser": "echo", "shares": []}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(native_webdav.WebDavAuthError, match="invalid mapping"):
        native_webdav.authenticate(
            "admin",
            "Admin-Password-123!",
            auth_path=_auth_store(tmp_path / "auth.json"),
            manifest_path=manifest,
            credential_directory=tmp_path / "credentials",
            validate_runtime_files=False,
        )


def test_systemd_credential_copy_may_be_read_only_but_never_group_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential = tmp_path / "webdav-sftp.key"
    mode = stat.S_IFREG | 0o444
    monkeypatch.setattr(native_webdav.os, "name", "posix")
    monkeypatch.setattr(native_webdav.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(
        type(credential),
        "lstat",
        lambda _self: SimpleNamespace(st_mode=mode, st_uid=1000),
    )
    native_webdav._safe_regular_file(credential)

    mode = stat.S_IFREG | 0o466
    with pytest.raises(native_webdav.WebDavAuthError, match="writable by group or others"):
        native_webdav._safe_regular_file(credential)
