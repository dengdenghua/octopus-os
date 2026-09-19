from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT_SERVICE = ROOT / "deploy/agent/echo-agent.service"
CREDENTIAL_SERVICE = ROOT / "deploy/agent/echo-docker-credential.service"
CONTROL_SERVICE = ROOT / "deploy/agent/echo-docker-control.service"
HEALTH_SERVICE = ROOT / "deploy/agent/echo-docker-control-health.service"
BROKER_SERVICE = ROOT / "deploy/agent/echo-native-storage-broker.service"
SYSUSERS = ROOT / "packaging/image/mkosi.extra/usr/lib/sysusers.d/echo-os.conf"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_native_agent_never_receives_the_docker_socket_or_plaintext_token() -> None:
    source = _source(AGENT_SERVICE)

    assert "Environment=ECHO_DOCKER_HOST=http://127.0.0.1:2375" in source
    assert "LoadCredential=echo.docker-proxy-token:/var/lib/echo-os/docker-proxy-token" in source
    assert "docker.sock" not in source
    assert "ECHO_DOCKER_PROXY_TOKEN=" not in source


def test_native_proxy_is_loopback_only_and_never_starts_as_root() -> None:
    source = _source(CONTROL_SERVICE)

    assert "ConditionPathExists=/var/run/docker.sock" in source
    assert (
        "Requires=docker.service echo-docker-credential.service echo-native-storage-broker.service"
    ) in source
    assert "Environment=ECHO_NATIVE_OS=1" in source
    assert (
        "ExecStart=/usr/bin/python3 -m appliance.docker_proxy --host 127.0.0.1 --port 2375"
    ) in source
    assert "User=echo-docker-control" in source
    assert "Group=echo" in source
    assert "SupplementaryGroups=docker" in source
    assert "CapabilityBoundingSet=\n" in source
    assert "User=root" not in source
    assert "IPAddressDeny=any" in source
    assert "IPAddressAllow=localhost" in source
    assert "0.0.0.0" not in source
    assert "ECHO_DOCKER_PROXY_TOKEN=" not in source


def test_native_proxy_has_a_dedicated_account_and_fixed_broker_uid_allowlist() -> None:
    control = _source(CONTROL_SERVICE)
    broker = _source(BROKER_SERVICE)
    sysusers = _source(SYSUSERS)

    assert 'u echo-docker-control - "Echo Docker Control" / -' in sysusers
    assert "User=echo-docker-control" in control
    assert "--user echo --user echo-docker-control --group echo" in broker


def test_native_proxy_observes_engine_storage_read_only() -> None:
    source = _source(CONTROL_SERVICE)

    assert "BindReadOnlyPaths=/var/lib/docker:/run/echo-host/docker-data" in source
    assert "Environment=ECHO_DOCKER_DATA_ROOT_MOUNT=/run/echo-host/docker-data" in source
    assert "Environment=ECHO_DOCKER_DATA_ROOT_EXPECTED=/var/lib/docker" in source


def test_credential_provisioner_has_one_private_persistent_write_surface() -> None:
    source = _source(CREDENTIAL_SERVICE)

    assert "StateDirectory=echo-os" in source
    assert "StateDirectoryMode=0700" in source
    assert "ReadWritePaths=/var/lib/echo-os" in source
    assert "ExecStart=/usr/bin/python3 -m appliance.docker_credential ensure" in source
    assert "AF_INET" not in source


def test_native_docker_health_is_a_boot_blessing_requirement() -> None:
    source = _source(HEALTH_SERVICE)

    assert "Requires=echo-docker-control.service" in source
    assert "ExecStart=/usr/bin/python3 -m appliance.native_docker_health" in source
    assert "LoadCredential=echo.docker-proxy-token:" in source
    assert "RequiredBy=boot-complete.target" in source
    assert "IPAddressDeny=any" in source
    assert "IPAddressAllow=localhost" in source
    assert "CapabilityBoundingSet=\n" in source
