from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (ROOT / "docker-compose.yml", ROOT / "deploy/appliance/docker-compose.yml")
OMV_OVERRIDE = ROOT / "deploy/omv/docker-compose.omv.yml"


def _socket_mounts(service: dict) -> list[str]:
    return [
        item
        for item in service.get("volumes", [])
        if isinstance(item, str) and "/var/run/docker.sock" in item
    ]


def _docker_data_mounts(service: dict) -> list[str]:
    return [
        item
        for item in service.get("volumes", [])
        if isinstance(item, str) and "/run/echo-host/docker-data" in item
    ]


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda path: path.parent.name)
def test_only_the_internal_least_privilege_sidecar_owns_docker_socket(
    compose_path: Path,
) -> None:
    compose = yaml.safe_load(compose_path.read_text())
    services = compose["services"]
    main = services["echo-os"]
    proxy = services["docker-control"]
    discovery = services["lan-discovery"]

    holders = [name for name, service in services.items() if _socket_mounts(service)]
    assert holders == ["docker-control"]
    assert _socket_mounts(proxy) == ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    assert _socket_mounts(main) == []
    assert _docker_data_mounts(main) == []
    assert "ECHO_DOCKER_SOCK" not in main["environment"]
    assert main["environment"]["ECHO_DOCKER_HOST"] == "http://docker-control:2375"
    required_proxy_token = "${ECHO_DOCKER_PROXY_TOKEN:?generate a 32+ character secret}"
    assert main["environment"]["ECHO_DOCKER_PROXY_TOKEN"] == required_proxy_token
    assert main["environment"]["ECHO_APPLIANCE_TRUSTED_HOSTS"] == (
        "${ECHO_APPLIANCE_TRUSTED_HOSTS:-}"
    )
    assert main["environment"]["ECHO_APPLIANCE_TRUSTED_ORIGINS"] == (
        "${ECHO_APPLIANCE_TRUSTED_ORIGINS:-}"
    )
    assert main["environment"]["FORWARDED_ALLOW_IPS"] == ("${ECHO_TRUSTED_PROXY_IPS:-127.0.0.1}")
    assert main["environment"]["ECHO_PUID"] == "${PUID:-1000}"
    assert main["environment"]["ECHO_PGID"] == "${PGID:-1000}"
    assert main["environment"]["ECHO_DEVICE_LINK_HOST"] == ("${ECHO_DEVICE_LINK_HOST:-}")
    assert main["environment"]["ECHO_DEVICE_LINK_PORT"] == ("${ECHO_DEVICE_LINK_PORT:-8765}")
    assert main["environment"]["ECHO_DEVICE_LINK_AUTO_HOST_FALLBACK"] == "0"
    expected_bind = (
        "${ECHO_BIND_ADDRESS:-0.0.0.0}"
        if compose_path.parent.name == "appliance"
        else "${ECHO_BIND_IP:-127.0.0.1}"
    )
    assert main["ports"] == [
        f"{expected_bind}:${{PORT:-8000}}:8000",
        "${ECHO_DEVICE_LINK_BIND_ADDRESS:-0.0.0.0}:"
        "${ECHO_DEVICE_LINK_PORT:-8765}:${ECHO_DEVICE_LINK_PORT:-8765}",
    ]
    if compose_path.parent.name == "appliance":
        assert main["environment"]["ECHO_UPLOAD_RESERVE_BYTES"] == (
            "${ECHO_UPLOAD_RESERVE_BYTES:-536870912}"
        )
        assert main["environment"]["ECHO_UPLOAD_MAX_BYTES"] == (
            "${ECHO_UPLOAD_MAX_BYTES:-53687091200}"
        )
        assert main["environment"]["ECHO_UPLOAD_STALE_SECONDS"] == (
            "${ECHO_UPLOAD_STALE_SECONDS:-86400}"
        )
        assert main["environment"]["ECHO_SHARE_QUOTAS_JSON"] == ("${ECHO_SHARE_QUOTAS_JSON:-}")
    assert main["user"] == "0:0"
    assert main["cap_drop"] == ["ALL"]
    assert set(main["cap_add"]) == {
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETGID",
        "SETUID",
    }
    assert "no-new-privileges:true" in main["security_opt"]

    assert proxy["entrypoint"] == ["python", "-m", "appliance.docker_proxy"]
    assert proxy["environment"]["ECHO_PUID"] == "${PUID:-1000}"
    assert proxy["environment"]["ECHO_PGID"] == "${PGID:-1000}"
    assert proxy["environment"]["ECHO_DOCKER_PROXY_TOKEN"] == required_proxy_token
    assert proxy["environment"]["ECHO_DOCKER_DATA_ROOT_EXPECTED"] == (
        "${ECHO_DOCKER_DATA_ROOT:-/var/lib/docker}"
    )
    assert proxy["environment"]["ECHO_DOCKER_DATA_ROOT_MOUNT"] == ("/run/echo-host/docker-data")
    assert _docker_data_mounts(proxy) == [
        "${ECHO_DOCKER_DATA_ROOT:-/var/lib/docker}:/run/echo-host/docker-data:ro"
    ]
    assert proxy["read_only"] is True
    assert proxy.get("ports") is None
    assert proxy["networks"] == ["docker-control"]
    assert proxy["cap_drop"] == ["ALL"]
    assert set(proxy["cap_add"]) == {"SETGID", "SETUID"}
    assert "no-new-privileges:true" in proxy["security_opt"]
    assert proxy["labels"]["sh.echo.control-protected"] == "true"
    assert proxy["labels"]["sh.echo.hub.data-copy-provider"] == "true"
    health_command = " ".join(proxy["healthcheck"]["test"])
    assert "X-Echo-Proxy-Token" in health_command
    assert "ECHO_DOCKER_PROXY_TOKEN" in health_command
    assert discovery["entrypoint"] == [
        "python",
        "-m",
        "appliance.lan_discovery_proxy",
    ]
    assert discovery["network_mode"] == "host"
    assert discovery.get("ports") is None
    assert discovery.get("volumes") is None
    assert discovery["user"] == "65534:65534"
    assert discovery["read_only"] is True
    assert discovery["cap_drop"] == ["ALL"]
    assert discovery.get("cap_add") is None
    assert discovery["pids_limit"] == 32
    assert discovery["mem_limit"] == "64m"
    assert "no-new-privileges:true" in discovery["security_opt"]
    assert discovery["labels"]["sh.echo.hide"] == "1"
    assert discovery["labels"]["sh.echo.control-protected"] == "true"
    assert discovery["labels"]["sh.echo.hub.lan-discovery-provider"] == "true"
    assert main["labels"]["sh.echo.control-protected"] == "true"
    assert main["labels"]["sh.echo.hub.nas-provider"] == "true"

    assert compose["networks"]["docker-control"]["internal"] is True
    assert "docker-control" in main["networks"]
    assert main["depends_on"]["docker-control"]["condition"] == "service_healthy"


def test_production_compose_pins_main_and_proxy_to_one_release_image() -> None:
    compose = yaml.safe_load((ROOT / "deploy/appliance/docker-compose.yml").read_text())
    services = compose["services"]

    assert services["echo-os"]["image"] == "${ECHO_OS_IMAGE:-echo-os:latest}"
    assert services["docker-control"]["image"] == services["echo-os"]["image"]
    assert services["lan-discovery"]["image"] == services["echo-os"]["image"]


def test_omv_override_mounts_only_the_read_bridge_into_the_main_service() -> None:
    override = yaml.safe_load(OMV_OVERRIDE.read_text())
    services = override["services"]

    assert list(services) == ["echo-os"]
    main = services["echo-os"]
    assert main["environment"] == {
        "ECHO_OMV_SOCKET": "/run/echo-omv/omv.sock",
        "ECHO_OMV_ADMIN_URL": "${ECHO_OMV_ADMIN_URL:-}",
        "ECHO_OMV_HEALTH_INTERVAL_SECONDS": ("${ECHO_OMV_HEALTH_INTERVAL_SECONDS:-300}"),
        "ECHO_OMV_TEMP_WARNING_C": "${ECHO_OMV_TEMP_WARNING_C:-50}",
        "ECHO_OMV_TEMP_CRITICAL_C": "${ECHO_OMV_TEMP_CRITICAL_C:-60}",
        "ECHO_OMV_CAPACITY_WARNING_PERCENT": ("${ECHO_OMV_CAPACITY_WARNING_PERCENT:-90}"),
        "ECHO_OMV_CAPACITY_CRITICAL_PERCENT": ("${ECHO_OMV_CAPACITY_CRITICAL_PERCENT:-95}"),
    }
    assert main["volumes"] == ["/run/echo-omv:/run/echo-omv:ro"]
    assert "ports" not in main


def test_omv_host_bridge_unit_has_no_network_or_elevated_capabilities() -> None:
    unit = (ROOT / "deploy/omv/echo-omv-bridge.service.example").read_text()

    assert "PrivateNetwork=true" in unit
    assert "RestrictAddressFamilies=AF_UNIX" in unit
    assert "NoNewPrivileges=true" in unit
    assert "CapabilityBoundingSet=\n" in unit
    assert "PrivateDevices=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/run/echo-omv" in unit
    assert "--socket /run/echo-omv/omv.sock" in unit
    assert "--omv-rpc /usr/sbin/omv-rpc" in unit
    assert "--lsblk /usr/bin/lsblk" in unit
    assert "ConditionFileIsExecutable=/usr/sbin/omv-rpc" in unit
    assert "ConditionFileIsExecutable=/usr/bin/lsblk" in unit
    assert "ConditionPathIsExecutable" not in unit
    assert "WorkingDirectory=/usr/lib/echo-os/omv-bridge" in unit
    assert "Environment=PYTHONPATH=/usr/lib/echo-os/omv-bridge" in unit
    assert "/opt/echo-os" not in unit
    assert "ProtectProc=invisible" in unit
    assert "ProcSubset=pid" not in unit
    assert "ListenStream=" not in unit


def test_appliance_build_pins_every_multiarch_base_image_by_digest() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]

    assert from_lines == [
        (
            "FROM node:20-alpine@sha256:"
            "fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 "
            "AS webui-builder"
        ),
        (
            "FROM python:3.12-slim@sha256:"
            "7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 "
            "AS agent-bundle-verifier"
        ),
        (
            "FROM python:3.12-slim@sha256:"
            "7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 "
            "AS py-builder"
        ),
        (
            "FROM python:3.12-slim@sha256:"
            "7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 "
            "AS runtime"
        ),
    ]


def test_appliance_python_install_uses_only_hash_locked_binary_dependencies() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "--require-hashes --only-binary=:all:" in dockerfile
    assert "-r agent-dist/build-requirements.lock" in dockerfile
    assert "-r agent-dist/runtime-requirements.lock" in dockerfile
    assert "--prefix=/build-tools" in dockerfile
    assert "pip install --prefix=/install --no-warn-script-location --no-deps" in dockerfile
    assert "-r agent-dist/requirements.txt" in dockerfile
    assert "COPY --from=agent-bundle-verifier /build/agent-dist/ ./agent-dist/" in dockerfile
    runtime_section = dockerfile.split("FROM python:3.12-slim", 3)[-1]
    assert "/build-tools" not in runtime_section


def test_appliance_crypto_dependency_cannot_regress_below_audited_fix_line() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = list(project["project"]["dependencies"])
    for extra in project["project"]["optional-dependencies"].values():
        declared.extend(extra)
    crypto_requirements = [item for item in declared if item.startswith("cryptography")]

    assert len(crypto_requirements) >= 4
    assert set(crypto_requirements) == {"cryptography>=50.0.0"}

    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    crypto = next(package for package in lock["package"] if package["name"] == "cryptography")
    version = tuple(int(part) for part in crypto["version"].split(".")[:3])
    assert version >= (50, 0, 0)
