from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT = ROOT / "deploy" / "appliance"
TLS_DIRECTORY = DEPLOYMENT / "tls"
TLS_OVERLAY = DEPLOYMENT / "docker-compose.tls.yml"
TLS_PREFLIGHT = TLS_DIRECTORY / "verify-tls-assets.sh"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _generate_certificate(
    destination: Path,
    *,
    host: str = "echo.home.example",
    days: int = 30,
    prefix: str = "echo",
) -> tuple[Path, Path]:
    certificate = destination / f"{prefix}.crt"
    private_key = destination / f"{prefix}.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            str(days),
            "-subj",
            f"/CN={host}",
            "-addext",
            f"subjectAltName=DNS:{host}",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    private_key.chmod(0o600)
    return certificate, private_key


def _preflight(certificate: Path, private_key: Path, host: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(TLS_PREFLIGHT), str(certificate), str(private_key), host],
        check=False,
        capture_output=True,
        text=True,
    )


def test_tls_overlay_pins_a_bounded_zero_capability_gateway() -> None:
    overlay = yaml.safe_load(TLS_OVERLAY.read_text())
    services = overlay["services"]
    gateway = services["tls-gateway"]
    backend = services["echo-os"]

    assert gateway["image"] == (
        "nginx:1.28.0-alpine-slim@sha256:"
        "ce2bd4775ed6859d35f47d65401ee9f35f1dd00b32ed05f0ce38b68aa1830195"
    )
    assert gateway["ports"] == [
        "${ECHO_TLS_BIND_ADDRESS:-0.0.0.0}:${ECHO_TLS_HTTPS_PORT:-443}:8443",
        "${ECHO_TLS_BIND_ADDRESS:-0.0.0.0}:${ECHO_TLS_HTTP_PORT:-80}:8080",
    ]
    assert gateway["read_only"] is True
    assert gateway["cap_drop"] == ["ALL"]
    assert gateway.get("cap_add") is None
    assert "no-new-privileges:true" in gateway["security_opt"]
    assert gateway["depends_on"] == {"echo-os": {"condition": "service_healthy"}}
    assert gateway["networks"]["default"]["ipv4_address"] == ("${ECHO_TLS_PROXY_IP:-172.30.90.2}")
    assert backend["environment"]["FORWARDED_ALLOW_IPS"] == ("${ECHO_TLS_PROXY_IP:-172.30.90.2}")
    assert "*" not in str(backend["environment"])

    image_lock = json.loads((TLS_DIRECTORY / "nginx-image.lock.json").read_text())
    assert image_lock == {
        "schema": "echo.tls-gateway-image-lock.v1",
        "registry": "registry-1.docker.io",
        "repository": "library/nginx",
        "tag": "1.28.0-alpine-slim",
        "indexDigest": "sha256:ce2bd4775ed6859d35f47d65401ee9f35f1dd00b32ed05f0ce38b68aa1830195",
        "platforms": {
            "linux/amd64": "sha256:eb8c00e3f75d52947cbfe3cc352671e70bcb78f850be8b64e6bf9652ee8853de",
            "linux/arm64": "sha256:cf0f6a2acd16fd75e9dd7fcc571fdcdf1acf1c29fee0194b240ef1faf30e1b62",
        },
    }


@pytest.mark.parametrize(
    "compose_path",
    [ROOT / "docker-compose.yml", DEPLOYMENT / "docker-compose.yml"],
)
def test_backend_binding_is_explicit_and_can_be_forced_to_loopback(
    compose_path: Path,
) -> None:
    compose = yaml.safe_load(compose_path.read_text())
    expected_bind = (
        "${ECHO_BIND_ADDRESS:-0.0.0.0}"
        if compose_path.parent.name == "appliance"
        else "${ECHO_BIND_IP:-127.0.0.1}"
    )
    assert compose["services"]["echo-os"]["ports"] == [
        f"{expected_bind}:${{PORT:-8000}}:8000",
        "${ECHO_DEVICE_LINK_BIND_ADDRESS:-0.0.0.0}:"
        "${ECHO_DEVICE_LINK_PORT:-8765}:${ECHO_DEVICE_LINK_PORT:-8765}",
    ]

    startup = (DEPLOYMENT / "start-tls.sh").read_text()
    assert "export ECHO_BIND_ADDRESS=127.0.0.1" in startup
    assert 'compose+=(--env-file "$appliance_env")' in startup
    assert 'compose+=(--env-file "$release_env")' in startup
    assert "compose+=(-f docker-compose.yml -f docker-compose.tls.yml)" in startup
    assert '"${compose[@]}" config --quiet' in startup
    assert startup.index("config --quiet") < startup.index("up -d")


def test_nginx_tls_edge_preserves_streams_without_logging_queries() -> None:
    config = (TLS_DIRECTORY / "nginx.conf").read_text()

    assert "ssl_protocols TLSv1.2 TLSv1.3;" in config
    assert "ssl_session_tickets off;" in config
    assert "proxy_pass http://echo-os:8000;" in config
    assert "proxy_set_header Host $http_host;" in config
    assert "proxy_set_header X-Forwarded-Proto https;" in config
    assert "proxy_set_header Upgrade $http_upgrade;" in config
    assert "proxy_request_buffering off;" in config
    assert "proxy_buffering off;" in config
    assert "client_max_body_size 0;" in config
    access_log_format = config.split("log_format echo_safe", 1)[1].split(";", 1)[0]
    assert "$request_uri" not in access_log_format
    assert "$args" not in access_log_format
    assert "$query_string" not in access_log_format
    assert "Strict-Transport-Security" not in config


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is required")
def test_tls_preflight_accepts_a_matching_private_certificate(tmp_path: Path) -> None:
    certificate, private_key = _generate_certificate(tmp_path)
    result = _preflight(certificate, private_key, "echo.home.example")

    assert result.returncode == 0, result.stderr
    assert "TLS certificate preflight passed" in result.stdout


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is required")
def test_tls_preflight_rejects_unsafe_key_permissions(tmp_path: Path) -> None:
    certificate, private_key = _generate_certificate(tmp_path)
    private_key.chmod(0o644)
    result = _preflight(certificate, private_key, "echo.home.example")

    assert result.returncode != 0
    assert "permissions must be 0400 or 0600" in result.stderr


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is required")
def test_tls_preflight_rejects_a_wrong_host_or_key(tmp_path: Path) -> None:
    certificate, private_key = _generate_certificate(tmp_path)
    wrong_host = _preflight(certificate, private_key, "other.home.example")
    assert wrong_host.returncode != 0
    assert "certificate SAN does not match" in wrong_host.stderr

    _, other_key = _generate_certificate(tmp_path, prefix="other")
    wrong_key = _preflight(certificate, other_key, "echo.home.example")
    assert wrong_key.returncode != 0
    assert "certificate and private key do not match" in wrong_key.stderr


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is required")
def test_tls_preflight_rejects_a_certificate_near_expiry(tmp_path: Path) -> None:
    certificate, private_key = _generate_certificate(tmp_path, days=1)
    result = _preflight(certificate, private_key, "echo.home.example")

    assert result.returncode != 0
    assert "expires in less than 7 days" in result.stderr


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is required")
def test_tls_startup_derives_trust_and_loopback_before_compose(tmp_path: Path) -> None:
    deployment = tmp_path / "appliance"
    tls_directory = deployment / "tls"
    binary_directory = tmp_path / "bin"
    tls_directory.mkdir(parents=True)
    binary_directory.mkdir()
    shutil.copy2(DEPLOYMENT / "start-tls.sh", deployment / "start-tls.sh")
    shutil.copy2(TLS_PREFLIGHT, tls_directory / "verify-tls-assets.sh")
    _generate_certificate(tls_directory)
    (deployment / "docker-compose.yml").write_text("services: {}\n")
    (deployment / "docker-compose.tls.yml").write_text("services: {}\n")

    docker_log = tmp_path / "docker.log"
    fake_docker = binary_directory / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        "printf '%s|%s|%s|%s\\n' \"$ECHO_BIND_ADDRESS\" "
        '"$ECHO_APPLIANCE_TRUSTED_HOSTS" '
        '"$ECHO_APPLIANCE_TRUSTED_ORIGINS" "$*" >> "$ECHO_TEST_DOCKER_LOG"\n'
    )
    fake_docker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{binary_directory}:{os.environ['PATH']}",
        "ECHO_TLS_HOST": "echo.home.example",
        "ECHO_TEST_DOCKER_LOG": str(docker_log),
    }

    result = subprocess.run(
        [str(deployment / "start-tls.sh"), "--build"],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    invocations = docker_log.read_text().splitlines()
    assert invocations == [
        "127.0.0.1|echo.home.example|https://echo.home.example|compose -f docker-compose.yml -f docker-compose.tls.yml config --quiet",
        "127.0.0.1|echo.home.example|https://echo.home.example|compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build",
    ]


@pytest.mark.parametrize(
    ("environment_change", "expected_error"),
    [
        ({"ECHO_TLS_HOST": "https://echo.home.example"}, "must be one exact DNS name or IP"),
        (
            {"ECHO_TLS_HOST": "bad..home.example"},
            "must be a valid ASCII DNS name or IPv4 address",
        ),
        (
            {"ECHO_TLS_HOST": "echo.home.example", "ECHO_TLS_HTTPS_PORT": "0"},
            "must be an integer between 1 and 65535",
        ),
        (
            {"ECHO_TLS_HOST": "echo.home.example", "ECHO_TLS_PROXY_IP": "172.30.91.2"},
            "proxy IP must be a usable address inside ECHO_TLS_SUBNET",
        ),
    ],
)
def test_tls_startup_fails_closed_before_cert_or_compose(
    environment_change: dict[str, str], expected_error: str
) -> None:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ECHO_TLS_")
    }
    result = subprocess.run(
        [str(DEPLOYMENT / "start-tls.sh")],
        env={**environment, **environment_change},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert "TLS preflight failed" not in result.stderr


def test_tls_secrets_are_ignored_and_not_present_in_the_repository() -> None:
    ignored = (TLS_DIRECTORY / ".gitignore").read_text().splitlines()
    assert {"echo.crt", "echo.key"}.issubset(ignored)
    assert not (TLS_DIRECTORY / "echo.crt").exists()
    assert not (TLS_DIRECTORY / "echo.key").exists()
    assert os.access(TLS_PREFLIGHT, os.X_OK)
    assert os.access(DEPLOYMENT / "start-tls.sh", os.X_OK)


def test_ci_runs_the_real_tls_gateway_and_https_security_verifier() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text())
    docker_job = workflow["jobs"]["docker-build"]
    steps = docker_job["steps"]
    by_name = {step.get("name"): step for step in steps if step.get("name")}

    certificate_step = by_name["Generate isolated TLS smoke certificate"]
    assert "subjectAltName=IP:127.0.0.1" in certificate_step["run"]
    assert "basicConstraints=critical,CA:TRUE" in certificate_step["run"]
    assert "extendedKeyUsage=serverAuth" in certificate_step["run"]
    assert "verify-tls-assets.sh" in certificate_step["run"]

    validation = by_name["Validate Compose models"]
    assert "docker-compose.tls.yml" in validation["run"]
    assert validation["env"] == {
        "ECHO_BIND_ADDRESS": "127.0.0.1",
        "ECHO_APPLIANCE_TRUSTED_HOSTS": "127.0.0.1",
        "ECHO_APPLIANCE_TRUSTED_ORIGINS": "https://127.0.0.1",
    }

    start = by_name["Start appliance behind the TLS gateway"]
    assert start["run"] == "./deploy/appliance/start-tls.sh --no-build"
    assert start["env"]["ECHO_TLS_HOST"] == "127.0.0.1"

    verify = by_name["Verify TLS gateway, loopback backend, and secure appliance contract"]
    source = verify["run"]
    assert "docker port echo-os 8000/tcp" in source
    assert "127.0.0.1:8000" in source
    assert "ReadonlyRootfs" in source
    assert "CapEff:" in source
    assert "NoNewPrivs:" in source
    assert "https://127.0.0.1/gateway-health" in source
    assert "--base-url https://127.0.0.1" in source
    assert verify["env"]["SSL_CERT_FILE"] == "/tmp/echo-tls-ca.crt"

    cleanup = by_name["Stop appliance"]
    assert cleanup["if"] == "always()"
    assert "docker-compose.tls.yml" in cleanup["run"]
