"""Immutable-image upgrade orchestration is backup-first and rollback-safe."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

TARGET_IMAGE = f"registry.example/echo-os@sha256:{'a' * 64}"
PREVIOUS_IMAGE = f"registry.example/echo-os@sha256:{'b' * 64}"


def _fake_upgrade_tools(tmp_path: Path) -> tuple[Path, Path]:
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "docker.log"
    docker = tools / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case " $* " in
  *" ps --status running --services "*)
    printf 'echo-os\\n'
    ;;
  *" appliance.state_backup export "*)
    for argument in "$@"; do
      case "$argument" in
        /backup/*.echo-backup)
          : > "$ECHO_BACKUP_DIR/${argument##*/}"
          chmod 600 "$ECHO_BACKUP_DIR/${argument##*/}"
          break
          ;;
      esac
    done
    ;;
  *" appliance.state_schema "*)
    if [[ "${FAKE_SCHEMA_MIGRATION:-0}" == "1" ]]; then
      printf '%s\\n' '{"compatible":true,"migrationRequired":true,"version":1,"currentRuntimeVersion":2}'
    else
      printf '%s\\n' '{"compatible":true,"migrationRequired":false,"version":1,"currentRuntimeVersion":1}'
    fi
    ;;
  *" up -d --no-build --wait "*)
    if [[ "${FAKE_KILL_TARGET_UP:-0}" == "1" ]] &&
       [[ -f "$ECHO_RELEASE_ENV" ]] &&
       grep -q "$FAKE_TARGET_IMAGE" "$ECHO_RELEASE_ENV"; then
      kill -KILL "$PPID"
      sleep 0.1
      exit 137
    fi
    if [[ "${FAKE_FAIL_TARGET_UP:-0}" == "1" ]] &&
       [[ -f "$ECHO_RELEASE_ENV" ]] &&
       grep -q "$FAKE_TARGET_IMAGE" "$ECHO_RELEASE_ENV"; then
      exit 42
    fi
    ;;
  *" inspect --format "*)
    if [[ -f "$ECHO_RELEASE_ENV" ]] && grep -q "$FAKE_TARGET_IMAGE" "$ECHO_RELEASE_ENV"; then
      printf '%s\\n' "$FAKE_TARGET_IMAGE"
    else
      printf '%s\\n' "$FAKE_PREVIOUS_IMAGE"
    fi
    ;;
esac
"""
    )
    docker.chmod(0o755)
    flock = tools / "flock"
    flock.write_text("#!/usr/bin/env bash\nexit 0\n")
    flock.chmod(0o755)
    return tools, log


def _run_upgrade(
    tmp_path: Path,
    *,
    target_image: str = TARGET_IMAGE,
    migration: bool = False,
    fail_target_up: bool = False,
    kill_target_up: bool = False,
    previous_release: bool = False,
    fail_storage: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    tools, log = _fake_upgrade_tools(tmp_path)
    release = tmp_path / "echo-release.env"
    if previous_release:
        release.write_text(f"ECHO_OS_IMAGE={PREVIOUS_IMAGE}\n")
        release.chmod(0o600)
    source = Path(__file__).parents[2] / "deploy" / "appliance"
    staged = tmp_path / "staged"
    staged.mkdir()
    for name in (
        "upgrade-appliance.sh",
        "backup-state.sh",
        "recover-appliance-upgrade.sh",
        "upgrade_transaction.py",
    ):
        shutil.copy2(source / name, staged / name)
    verifier = staged / "external_storage.py"
    verifier.write_text(
        """import os
import sys
with open(os.environ["FAKE_EXTERNAL_STORAGE_LOG"], "a", encoding="utf-8") as output:
    output.write(" ".join(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_EXTERNAL_STORAGE_FAIL") == "1":
    raise SystemExit(42)
print("ECHO_EXTERNAL_STORAGE_READY test=1")
"""
    )
    mountpoint = tmp_path / "external"
    backups = mountpoint / "backups"
    backups.mkdir(parents=True)
    script = staged / "upgrade-appliance.sh"
    environment = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "ECHO_DOCKER_BIN": str(tools / "docker"),
        "ECHO_BACKUP_DIR": str(backups),
        "ECHO_BACKUP_MOUNTPOINT": str(mountpoint),
        "ECHO_BACKUP_PASSPHRASE": "correct horse battery staple",
        "ECHO_RELEASE_ENV": str(release),
        "ECHO_MAINTENANCE_LOCK": str(tmp_path / "echo-maintenance.lock"),
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_SCHEMA_MIGRATION": "1" if migration else "0",
        "FAKE_FAIL_TARGET_UP": "1" if fail_target_up else "0",
        "FAKE_KILL_TARGET_UP": "1" if kill_target_up else "0",
        "FAKE_TARGET_IMAGE": TARGET_IMAGE,
        "FAKE_PREVIOUS_IMAGE": PREVIOUS_IMAGE,
        "FAKE_EXTERNAL_STORAGE_LOG": str(tmp_path / "external-storage.log"),
        "FAKE_EXTERNAL_STORAGE_FAIL": "1" if fail_storage else "0",
    }
    result = subprocess.run(
        [str(script), target_image],
        cwd=script.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    commands = log.read_text().splitlines() if log.exists() else []
    return result, commands, release


def test_upgrade_rejects_mutable_tag_before_touching_docker(tmp_path) -> None:
    result, commands, release = _run_upgrade(
        tmp_path,
        target_image="registry.example/echo-os:latest",
    )

    assert result.returncode != 0
    assert "immutable registry reference" in result.stderr
    assert commands == []
    assert not release.exists()


def test_upgrade_is_backup_first_and_persists_digest_selection(tmp_path) -> None:
    result, commands, release = _run_upgrade(tmp_path)

    assert result.returncode == 0, result.stderr
    export = next(i for i, command in enumerate(commands) if " export " in command)
    pull = next(i for i, command in enumerate(commands) if command.startswith("pull "))
    schema = next(i for i, command in enumerate(commands) if " appliance.state_schema " in command)
    up = next(i for i, command in enumerate(commands) if " up -d " in command)
    assert export < pull < schema < up
    assert release.read_text() == f"ECHO_OS_IMAGE={TARGET_IMAGE}\n"
    assert sum("inspect --format" in command for command in commands) == 3


def test_upgrade_refuses_unverified_backup_before_pull_or_switch(tmp_path) -> None:
    result, commands, release = _run_upgrade(tmp_path, fail_storage=True)

    assert result.returncode != 0
    assert "not a verified active external filesystem" in result.stderr
    assert not any(command.startswith("pull ") for command in commands)
    assert not any(" appliance.state_backup export " in command for command in commands)
    assert not release.exists()


def test_automatic_upgrade_refuses_target_requiring_schema_migration(tmp_path) -> None:
    result, commands, release = _run_upgrade(tmp_path, migration=True)

    assert result.returncode != 0
    assert "reviewed migration runbook" in result.stderr
    assert not release.exists()
    assert not any(" up -d " in command for command in commands)


def test_unhealthy_target_restores_previous_release_and_restarts_it(tmp_path) -> None:
    result, commands, release = _run_upgrade(
        tmp_path,
        fail_target_up=True,
        previous_release=True,
    )

    assert result.returncode != 0
    assert release.read_text() == f"ECHO_OS_IMAGE={PREVIOUS_IMAGE}\n"
    assert sum(" up -d " in command for command in commands) == 2
    assert "restoring the previous image selection" in result.stderr
    assert not (release.parent / ".echo-upgrade-transaction.json").exists()


def test_abrupt_upgrade_death_leaves_a_durable_transaction_for_recovery(tmp_path) -> None:
    result, _commands, release = _run_upgrade(
        tmp_path,
        kill_target_up=True,
        previous_release=True,
    )

    staged = tmp_path / "staged"
    transaction = staged / ".echo-upgrade-transaction.json"
    assert result.returncode < 0
    assert transaction.is_file()
    assert transaction.stat().st_mode & 0o777 == 0o600
    assert release.read_text() == f"ECHO_OS_IMAGE={TARGET_IMAGE}\n"

    tools = tmp_path / "tools"
    environment = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "ECHO_DOCKER_BIN": str(tools / "docker"),
        "ECHO_RELEASE_ENV": str(release),
        "ECHO_MAINTENANCE_LOCK": str(tmp_path / "echo-maintenance.lock"),
        "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
        "FAKE_KILL_TARGET_UP": "0",
        "FAKE_FAIL_TARGET_UP": "0",
        "FAKE_TARGET_IMAGE": TARGET_IMAGE,
        "FAKE_PREVIOUS_IMAGE": PREVIOUS_IMAGE,
    }
    recovered = subprocess.run(
        [str(staged / "recover-appliance-upgrade.sh")],
        cwd=staged,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert recovered.returncode == 0, recovered.stderr
    assert release.read_text() == f"ECHO_OS_IMAGE={PREVIOUS_IMAGE}\n"
    assert not transaction.exists()
    assert "upgrade recovery complete" in recovered.stdout
