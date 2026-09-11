from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
WEBDAV = REPOSITORY / "deploy" / "webdav"


def _jail_module() -> ModuleType:
    path = WEBDAV / "echo_webdav_jails.py"
    spec = importlib.util.spec_from_file_location("echo_webdav_jails_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mountpoint_uses_util_linux_exit_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _jail_module()
    monkeypatch.setattr(
        module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0)
    )
    assert module._is_mountpoint(Path("/run/echo-webdav/jails/echo/shares/photos")) is True

    monkeypatch.setattr(
        module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=32)
    )
    assert module._is_mountpoint(Path("/run/echo-webdav/jails/echo/shares/photos")) is False

    monkeypatch.setattr(
        module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1)
    )
    with pytest.raises(module.JailSyncError, match="unexpected status"):
        module._is_mountpoint(Path("/run/echo-webdav/jails/echo/shares/photos"))


def test_jail_projection_hides_inactive_and_unreadable_shares() -> None:
    module = _jail_module()
    accounts = {
        "admin": {"active": True, "omv_username": None},
        "alice": {"active": True, "omv_username": "alice"},
        "disabled": {"active": False, "omv_username": "disabled"},
    }
    shares = [
        {
            "uuid": "11111111-1111-4111-8111-111111111111",
            "name": "photos",
            "source": "/data/one/photos",
        },
        {
            "uuid": "22222222-2222-4222-8222-222222222222",
            "name": "private",
            "source": "/data/one/private",
        },
    ]

    desired = module.build_desired(
        accounts,
        shares,
        posix_lookup=lambda name: SimpleNamespace(pw_uid=1000, pw_dir=f"/home/{name}"),
        access_check=lambda user, path: user == "echo" or path.name == "photos",
    )

    assert set(desired) == {"admin", "alice"}
    assert desired["admin"]["posixUser"] == "echo"
    assert [item["name"] for item in desired["admin"]["shares"]] == [
        "photos",
        "private",
    ]
    assert [item["name"] for item in desired["alice"]["shares"]] == ["photos"]


def test_jail_source_state_reads_nothing_until_publication_is_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _jail_module()
    from appliance import native_webdav_control

    monkeypatch.setattr(native_webdav_control, "publication_enabled", lambda **_kwargs: False)
    monkeypatch.setattr(
        module,
        "read_auth_store",
        lambda *_args, **_kwargs: pytest.fail("disabled publication read the auth store"),
    )

    assert module._source_state() == ({}, [])


def test_duplicate_share_names_get_stable_unambiguous_webdav_names() -> None:
    module = _jail_module()
    shares = [
        {
            "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "name": "media",
            "source": "/data/one/media",
        },
        {
            "uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "name": "media",
            "source": "/data/two/media",
        },
    ]
    desired = module.build_desired(
        {"admin": {"active": True, "omv_username": None}},
        shares,
        posix_lookup=lambda _name: SimpleNamespace(pw_uid=1000, pw_dir="/home/echo"),
        access_check=lambda _user, _path: True,
    )
    assert [item["name"] for item in desired["admin"]["shares"]] == [
        "media--aaaaaaaa",
        "media--bbbbbbbb",
    ]


def test_duplicate_posix_mapping_is_rejected() -> None:
    module = _jail_module()
    with pytest.raises(module.JailSyncError, match="duplicated"):
        module.build_desired(
            {
                "admin": {"active": True, "omv_username": None},
                "echo-login": {"active": True, "omv_username": "echo"},
            },
            [
                {
                    "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "name": "media",
                    "source": "/data/media",
                }
            ],
            posix_lookup=lambda _name: SimpleNamespace(pw_uid=1000, pw_dir="/home/echo"),
            access_check=lambda _user, _path: True,
        )


def test_webdav_services_keep_plaintext_and_privileged_boundaries_local() -> None:
    gateway = (WEBDAV / "echo-webdav.service").read_text(encoding="utf-8")
    sshd_service = (WEBDAV / "echo-webdav-sshd.service").read_text(encoding="utf-8")
    jail_service = (WEBDAV / "echo-webdav-jails.service").read_text(encoding="utf-8")
    refresh_service = (WEBDAV / "echo-webdav-refresh.service").read_text(encoding="utf-8")
    sshd = (WEBDAV / "sshd_config").read_text(encoding="utf-8")

    assert "User=echo" in gateway
    assert "--addr=127.0.0.1:5005" in gateway
    assert "--auth-proxy=/usr/lib/echo-os/webdav/echo-webdav-auth" in gateway
    assert "LoadCredential=webdav-sftp.key:" in gateway
    assert "LoadCredential=webdav-known-hosts:" in gateway
    assert "CapabilityBoundingSet=\n" in gateway
    assert "ListenAddress 127.0.0.1" in sshd
    assert "Port 22022" in sshd
    assert "AuthenticationMethods publickey" in sshd
    assert "PasswordAuthentication no" in sshd
    assert "ChrootDirectory /run/echo-webdav/jails/%u" in sshd
    assert "ForceCommand internal-sftp" in sshd
    assert "DisableForwarding yes" in sshd
    assert "CAP_SYS_CHROOT" in sshd_service
    assert "CAP_SYS_ADMIN" not in sshd_service
    assert (
        "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_SETGID CAP_SETUID CAP_SYS_ADMIN"
        in jail_service
    )
    assert "RestrictAddressFamilies=AF_UNIX" in jail_service
    assert "ProtectSystem=" not in jail_service
    assert "ProtectControlGroups=" not in jail_service
    assert "ProtectKernelTunables=" not in jail_service
    assert "CAP_SYS_ADMIN" in refresh_service
    assert "RestrictAddressFamilies=AF_UNIX" in refresh_service
    assert "ProtectSystem=" not in refresh_service
    assert "ProtectControlGroups=" not in refresh_service
    assert "ProtectKernelTunables=" not in refresh_service


def test_gateway_key_stays_private_while_sshd_can_read_public_authorization() -> None:
    jails = (WEBDAV / "echo_webdav_jails.py").read_text(encoding="utf-8")
    assert "_safe_directory(GATEWAY_ROOT, 0o711)" in jails
    assert "_atomic_write(GATEWAY_KEY, temporary_key.read_bytes(), 0o400)" in jails
    assert "_atomic_write(AUTHORIZED_KEYS" in jails


def test_jail_refresh_never_normalizes_an_active_bind_target() -> None:
    jails = (WEBDAV / "echo_webdav_jails.py").read_text(encoding="utf-8")
    mounted_branch = jails.split("if mounted:", 1)[1].split("if not mounted:", 1)[0]
    unmounted_branch = jails.split("if not mounted:", 1)[1]
    assert "_safe_directory(target" not in mounted_branch
    assert "_safe_directory(target, 0o755)" in unmounted_branch


def test_nginx_exposes_webdav_only_through_tls_and_loopback_proxy() -> None:
    nginx = (REPOSITORY / "deploy/provision/base/echo-nginx.conf").read_text(encoding="utf-8")
    location = nginx.split("location ^~ /webdav/ {", 1)[1].split("\n    }", 1)[0]
    assert "if ($scheme = http) { return 403; }" in location
    assert "proxy_pass http://127.0.0.1:5005;" in location
    assert "proxy_set_header Host $http_host;" in location
    assert "proxy_set_header X-Forwarded-Host $http_host;" in location
    assert "proxy_request_buffering off;" in location
    assert "proxy_buffering off;" in location
    assert "/var/lib/echo-os/device-tls/device.crt" in nginx
    assert "/var/lib/echo-os/device-tls/device.key" in nginx


def test_device_tls_identity_is_generated_per_device_with_modern_sans() -> None:
    script = (WEBDAV / "echo-device-tls").read_text(encoding="utf-8")
    assert "openssl req -x509" in script
    assert "-newkey rsa:3072" in script
    assert "basicConstraints=critical,CA:FALSE" in script
    assert "extendedKeyUsage=serverAuth" in script
    assert "subjectAltName=DNS:$device_name,DNS:$device_name.local,IP:127.0.0.1" in script
    assert "chmod 0600" in script
    assert "key_fingerprint" in script and "cert_fingerprint" in script
