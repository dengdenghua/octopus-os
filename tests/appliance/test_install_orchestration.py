from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/appliance/install-appliance.sh"
IMAGE = f"ghcr.io/echo-os/echo-os@sha256:{'a' * 64}"


@pytest.fixture
def fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    command = tmp_path / "docker"
    log = tmp_path / "docker.log"
    command.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  *"inspect --format"*) printf '%s\\n' "$FAKE_IMAGE" ;;
  *"compose"*" up "*)
    if [[ "${FAKE_UP_FAIL:-0}" == "1" ]]; then exit 1; fi
    ;;
esac
"""
    )
    command.chmod(0o755)
    return command, log


def _run(
    tmp_path: Path,
    fake_docker: tuple[Path, Path],
    *,
    release: str = f"ECHO_OS_IMAGE={IMAGE}\n",
    appliance: str | None = "NAS_STORAGE=/srv/echo\nPORT=9443\n",
    fail_up: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    docker, log = fake_docker
    release_path = tmp_path / "echo-release.env"
    release_path.write_text(release)
    appliance_path = tmp_path / "appliance.env"
    if appliance is not None:
        appliance_path.write_text(appliance)
    environment = {
        **os.environ,
        "ECHO_DOCKER_BIN": str(docker),
        "ECHO_RELEASE_ENV": str(release_path),
        "ECHO_APPLIANCE_ENV": str(appliance_path),
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_IMAGE": IMAGE,
        "FAKE_UP_FAIL": "1" if fail_up else "0",
    }
    result = subprocess.run(
        [str(SCRIPT)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    commands = log.read_text().splitlines() if log.exists() else []
    return result, commands


def test_install_uses_separate_configuration_and_immutable_release(tmp_path, fake_docker) -> None:
    result, commands = _run(tmp_path, fake_docker)

    assert result.returncode == 0, result.stderr
    assert commands[0] == "compose version"
    config = next(command for command in commands if " config --quiet" in command)
    up = next(command for command in commands if " up -d " in command)
    assert config.index("--env-file " + str(tmp_path / "appliance.env")) < config.index(
        "--env-file " + str(tmp_path / "echo-release.env")
    )
    assert f"pull {IMAGE}" in commands
    assert "--no-build --wait --wait-timeout 180" in up
    assert sum("inspect --format" in command for command in commands) == 2
    assert IMAGE in result.stdout


@pytest.mark.parametrize(
    "release,match",
    [
        ("ECHO_OS_IMAGE=ghcr.io/echo-os/echo-os:latest\n", "immutable"),
        (f"ECHO_OS_IMAGE={IMAGE}\nUNEXPECTED=1\n", "unsupported"),
        (f"ECHO_OS_IMAGE={IMAGE}\nECHO_OS_IMAGE={IMAGE}\n", "more than once"),
    ],
)
def test_install_rejects_malformed_release_before_pull(
    tmp_path, fake_docker, release: str, match: str
) -> None:
    result, commands = _run(tmp_path, fake_docker, release=release)

    assert result.returncode != 0
    assert match in result.stderr
    assert not any(command.startswith("pull ") for command in commands)


def test_install_rejects_image_override_in_appliance_environment(tmp_path, fake_docker) -> None:
    result, commands = _run(
        tmp_path,
        fake_docker,
        appliance=f"PORT=9000\nECHO_OS_IMAGE={IMAGE}\n",
    )

    assert result.returncode != 0
    assert "must not override ECHO_OS_IMAGE" in result.stderr
    assert not any(command.startswith("pull ") for command in commands)


def test_install_propagates_health_failure(tmp_path, fake_docker) -> None:
    result, commands = _run(tmp_path, fake_docker, fail_up=True)

    assert result.returncode != 0
    assert any(" up -d " in command for command in commands)
    assert not any("inspect --format" in command for command in commands)
