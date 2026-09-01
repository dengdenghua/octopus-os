"""Host recovery promotes only a verified restored state and rolls back failures."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path


def _fake_tools(tmp_path: Path) -> tuple[Path, Path]:
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "docker.log"
    docker = tools / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
case " $* " in
  *" ps --status running --services "*)
    if [[ "${FAKE_WAS_RUNNING:-1}" == "1" ]]; then
      printf 'echo-os\n'
    fi
    ;;
  *" appliance.state_backup verify "*)
    printf '%s\n' '{"encrypted":true,"stateCompatible":true}'
    ;;
  *" appliance.state_backup restore "*)
    [[ "${FAKE_FAIL_RESTORE:-0}" != "1" ]] || exit 42
    for argument in "$@"; do
      case "$argument" in
        /state-parent/*/restored)
          restored="$FAKE_STATE_PARENT/${argument#/state-parent/}"
          mkdir -p "$restored"
          printf 'restored\n' > "$restored/restored.txt"
          ;;
      esac
    done
    printf '%s\n' '{"encrypted":true,"restoredTo":"staging"}'
    ;;
  *" appliance.state_schema "*)
    printf '%s\n' '{"compatible":true,"migrationRequired":false,"version":2}'
    ;;
  *" appliance.state_recovery --state-dir /state-parent/"*)
    if [[ "${FAKE_REMOVE_STAGED_AFTER_INSPECT:-0}" == "1" ]]; then
      for argument in "$@"; do
        case "$argument" in
          /state-parent/*/restored)
            restored="$FAKE_STATE_PARENT/${argument#/state-parent/}"
            rm -f "$restored/restored.txt"
            rmdir "$restored"
            ;;
        esac
      done
    fi
    if [[ "${FAKE_MUTATE_BACKUP:-0}" == "1" ]]; then
      printf 'replacement\n' >> "$FAKE_BACKUP_PATH"
    fi
    printf '%s\n' '{"ok":true,"readOnlyInspection":true}'
    ;;
  *" exec -T echo-os python -m appliance.state_recovery "*)
    [[ "${FAKE_FAIL_LIVE_VERIFY:-0}" != "1" ]] || exit 43
    printf '%s\n' '{"ok":true,"readOnlyInspection":true}'
    ;;
  *" up -d --no-build --wait "*)
    [[ "${FAKE_FAIL_UP:-0}" != "1" ]] || exit 44
    ;;
esac
"""
    )
    docker.chmod(0o755)
    flock = tools / "flock"
    flock.write_text("#!/usr/bin/env bash\nexit 0\n")
    flock.chmod(0o755)
    return tools, log


def _deployment(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = Path(__file__).parents[2] / "deploy" / "appliance" / "restore-state.sh"
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    script = deploy / "restore-state.sh"
    shutil.copy2(source, script)
    script.chmod(0o755)
    (deploy / "docker-compose.yml").write_text("services: {}\n")
    state = deploy / "data"
    state.mkdir()
    (state / "old.txt").write_text("original\n")
    backup = tmp_path / "external" / "echo-state-20260827T010000Z.echo-backup"
    backup.parent.mkdir()
    backup.write_bytes(b"authenticated-encrypted-backup")
    return script, state, backup


def _run(
    tmp_path: Path,
    *,
    confirmed: bool,
    was_running: bool = True,
    fail_restore: bool = False,
    fail_live_verify: bool = False,
    remove_staged_after_inspect: bool = False,
    mutate_backup: bool = False,
):
    tools, log = _fake_tools(tmp_path)
    script, state, backup = _deployment(tmp_path)
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    environment = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "ECHO_DOCKER_BIN": str(tools / "docker"),
        "ECHO_HOST_PYTHON": os.environ.get("PYTHON", "python3"),
        "ECHO_BACKUP_PASSPHRASE": "correct horse battery staple",
        "ECHO_RESTORE_WAIT_TIMEOUT": "60",
        "ECHO_MAINTENANCE_LOCK": str(tmp_path / "echo-maintenance.lock"),
        "ECHO_RESTORE_CONFIRM": (f"RESTORE sha256:{digest} TO {state}" if confirmed else ""),
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_STATE_PARENT": str(state.parent),
        "FAKE_WAS_RUNNING": "1" if was_running else "0",
        "FAKE_FAIL_RESTORE": "1" if fail_restore else "0",
        "FAKE_FAIL_LIVE_VERIFY": "1" if fail_live_verify else "0",
        "FAKE_REMOVE_STAGED_AFTER_INSPECT": ("1" if remove_staged_after_inspect else "0"),
        "FAKE_MUTATE_BACKUP": "1" if mutate_backup else "0",
        "FAKE_BACKUP_PATH": str(backup),
    }
    result = subprocess.run(
        [str(script), str(backup)],
        cwd=script.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    commands = log.read_text().splitlines() if log.exists() else []
    return result, commands, state, digest


def test_preview_verifies_backup_but_does_not_stop_or_change_live_state(tmp_path) -> None:
    result, commands, state, digest = _run(tmp_path, confirmed=False)

    assert result.returncode == 2
    assert "No live state was changed" in result.stdout
    assert f"RESTORE sha256:{digest} TO {state}" in result.stdout
    assert (state / "old.txt").read_text() == "original\n"
    assert len(commands) == 1 and " appliance.state_backup verify " in commands[0]
    assert not list(state.parent.glob(".data.echo-rollback-*"))


def test_verified_restore_promotes_new_state_and_retains_previous_directory(tmp_path) -> None:
    result, commands, state, _digest = _run(tmp_path, confirmed=True)

    assert result.returncode == 0, result.stderr
    assert (state / "restored.txt").read_text() == "restored\n"
    rollback = list(state.parent.glob(".data.echo-rollback-*"))
    assert len(rollback) == 1
    assert (rollback[0] / "old.txt").read_text() == "original\n"
    order = {
        marker: next(i for i, command in enumerate(commands) if marker in command)
        for marker in (
            " appliance.state_backup verify ",
            " stop ",
            " appliance.state_backup restore ",
            " appliance.state_schema ",
            " up -d --no-build --wait ",
            " exec -T echo-os python -m appliance.state_recovery ",
        )
    }
    assert (
        order[" appliance.state_backup verify "]
        < order[" stop "]
        < order[" appliance.state_backup restore "]
        < order[" appliance.state_schema "]
        < order[" up -d --no-build --wait "]
        < order[" exec -T echo-os python -m appliance.state_recovery "]
    )


def test_failed_staging_restore_restarts_original_without_promoting(tmp_path) -> None:
    result, commands, state, _digest = _run(tmp_path, confirmed=True, fail_restore=True)

    assert result.returncode != 0
    assert (state / "old.txt").read_text() == "original\n"
    assert not list(state.parent.glob(".data.echo-rollback-*"))
    assert any(" up -d --no-build --wait " in command for command in commands)
    assert "Unpromoted restore staging preserved" in result.stderr


def test_failed_live_validation_rolls_directory_back_and_preserves_failed_state(tmp_path) -> None:
    result, commands, state, _digest = _run(
        tmp_path,
        confirmed=True,
        fail_live_verify=True,
    )

    assert result.returncode != 0
    assert (state / "old.txt").read_text() == "original\n"
    failed = list(state.parent.glob(".data.echo-failed-*"))
    assert len(failed) == 1
    assert (failed[0] / "restored.txt").read_text() == "restored\n"
    assert not list(state.parent.glob(".data.echo-rollback-*"))
    assert sum(" up -d --no-build --wait " in command for command in commands) == 2
    assert "rolling back the directory promotion" in result.stderr


def test_originally_stopped_service_is_stopped_again_after_success(tmp_path) -> None:
    result, commands, state, _digest = _run(
        tmp_path,
        confirmed=True,
        was_running=False,
    )

    assert result.returncode == 0, result.stderr
    assert (state / "restored.txt").is_file()
    stop_commands = [command for command in commands if " stop " in command]
    assert len(stop_commands) == 1
    assert commands.index(stop_commands[0]) > next(
        i for i, command in enumerate(commands) if " exec -T echo-os " in command
    )


def test_interrupted_second_rename_restores_displaced_live_directory(tmp_path) -> None:
    result, _commands, state, _digest = _run(
        tmp_path,
        confirmed=True,
        remove_staged_after_inspect=True,
    )

    assert result.returncode != 0
    assert (state / "old.txt").read_text() == "original\n"
    assert not list(state.parent.glob(".data.echo-rollback-*"))
    assert "promotion was interrupted" in result.stderr


def test_backup_replacement_after_staging_is_detected_before_promotion(tmp_path) -> None:
    result, _commands, state, _digest = _run(
        tmp_path,
        confirmed=True,
        mutate_backup=True,
    )

    assert result.returncode != 0
    assert (state / "old.txt").read_text() == "original\n"
    assert not list(state.parent.glob(".data.echo-rollback-*"))
    assert "verified backup changed before promotion" in result.stderr
