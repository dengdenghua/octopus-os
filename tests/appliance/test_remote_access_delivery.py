from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT = ROOT / "deploy" / "appliance"
OVERLAY = DEPLOYMENT / "docker-compose.remote-access.yml"
START = DEPLOYMENT / "start-remote-access.sh"


def test_tailscale_overlay_is_pinned_private_and_least_privileged() -> None:
    overlay = yaml.safe_load(OVERLAY.read_text())
    service = overlay["services"]["tailscale"]

    assert service["image"] == (
        "tailscale/tailscale:v1.102.3@sha256:"
        "8c42c4574ab066384fcb72f69e086a2ff1dd3652eb6f56856cee34bcf0d2f680"
    )
    assert service.get("ports") is None
    assert service.get("expose") is None
    assert service["networks"] == {
        "remote-access": {"ipv4_address": "${ECHO_TAILSCALE_PROXY_IP:-172.30.91.2}"}
    }
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service.get("cap_add") is None
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["environment"]["TS_USERSPACE"] == "true"
    assert service["environment"]["TS_ACCEPT_DNS"] == "false"
    assert service["environment"]["TS_AUTHKEY"] == ("file:/run/secrets/tailscale-auth-key")
    assert service["secrets"] == ["tailscale-auth-key"]
    assert service["labels"]["sh.echo.control-protected"] == "true"
    assert overlay["services"]["echo-os"]["environment"] == {
        "ECHO_REMOTE_ACCESS_PROVIDER": "tailscale-sidecar",
        "ECHO_REMOTE_ACCESS_URL": (
            "https://${ECHO_TAILSCALE_DNS_NAME:?set ECHO_TAILSCALE_DNS_NAME}"
        ),
        "FORWARDED_ALLOW_IPS": "${ECHO_TAILSCALE_PROXY_IP:-172.30.91.2}",
    }
    assert overlay["services"]["echo-os"]["networks"] == {"remote-access": {}}
    assert overlay["networks"]["remote-access"] == {
        "driver": "bridge",
        "ipam": {"config": [{"subnet": "${ECHO_TAILSCALE_SUBNET:-172.30.91.0/24}"}]},
    }
    assert overlay["secrets"]["tailscale-auth-key"]["file"] == (
        "${ECHO_TAILSCALE_AUTHKEY_FILE:?set ECHO_TAILSCALE_AUTHKEY_FILE}"
    )

    lock = json.loads((DEPLOYMENT / "remote-access" / "tailscale-image.lock.json").read_text())
    assert service["image"].endswith(f"@{lock['indexDigest']}")
    assert set(lock["platforms"]) == {"linux/amd64", "linux/arm64"}
    assert all(value.startswith("sha256:") for value in lock["platforms"].values())


def test_tailscale_serve_config_terminates_https_and_only_proxies_echo() -> None:
    config = json.loads((DEPLOYMENT / "remote-access" / "tailscale-serve.json").read_text())

    assert config == {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {"${TS_CERT_DOMAIN}:443": {"Handlers": {"/": {"Proxy": "http://echo-os:8000"}}}},
    }


def test_remote_access_startup_validates_secret_without_printing_it(tmp_path: Path) -> None:
    auth_key = tmp_path / "tailscale-auth.key"
    auth_key.write_text("tskey-auth-unit-test-private-value")
    auth_key.chmod(0o600)
    log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$ECHO_TEST_DOCKER_LOG"\n'
        'printf \'%s\\n\' "${ECHO_TAILSCALE_DNS_NAME:-}" >> "$ECHO_TEST_DOCKER_LOG"\n'
    )
    fake_docker.chmod(0o755)
    environment = {
        **os.environ,
        "ECHO_DOCKER_BIN": str(fake_docker),
        "ECHO_TEST_DOCKER_LOG": str(log),
        "ECHO_TAILSCALE_DNS_NAME": "echo-os.example.ts.net",
    }

    result = subprocess.run(
        [str(START), str(auth_key), "--build"],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr + log.read_text()
    assert "tskey-auth-unit-test-private-value" not in output
    calls = log.read_text().splitlines()
    assert calls[0].endswith(
        "-f docker-compose.yml -f docker-compose.remote-access.yml config --quiet"
    )
    assert calls[2].endswith(
        "-f docker-compose.yml -f docker-compose.remote-access.yml up -d --build"
    )


def test_remote_access_startup_rejects_weak_secret_permissions(tmp_path: Path) -> None:
    auth_key = tmp_path / "tailscale-auth.key"
    auth_key.write_text("tskey-auth-unit-test-private-value")
    auth_key.chmod(0o644)

    result = subprocess.run(
        [str(START), str(auth_key)],
        env={**os.environ, "ECHO_TAILSCALE_DNS_NAME": "echo-os.example.ts.net"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "permissions must be 0400 or 0600" in result.stderr
