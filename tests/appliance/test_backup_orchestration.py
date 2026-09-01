"""Host-side backup orchestration preserves service state and ordering."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _fake_host_tools(tmp_path: Path) -> tuple[Path, Path]:
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
    if [[ "${FAKE_DOCKER_FAIL_EXPORT:-0}" == "1" ]]; then
      exit 42
    fi
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
esac
"""
    )
    docker.chmod(0o755)
    flock = tools / "flock"
    flock.write_text("#!/usr/bin/env bash\nexit 0\n")
    flock.chmod(0o755)
    return tools, log


def _stage_backup_script(tmp_path: Path) -> Path:
    source = Path(__file__).parents[2] / "deploy" / "appliance"
    staged = tmp_path / "staged"
    staged.mkdir()
    shutil.copy2(source / "backup-state.sh", staged / "backup-state.sh")
    verifier = staged / "external_storage.py"
    verifier.write_text(
        """#!/usr/bin/env python3
import os
import sys
with open(os.environ["FAKE_EXTERNAL_STORAGE_LOG"], "a", encoding="utf-8") as output:
    output.write(" ".join(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_EXTERNAL_STORAGE_FAIL") == "1":
    raise SystemExit(42)
print("ECHO_EXTERNAL_STORAGE_READY test=1")
"""
    )
    return staged / "backup-state.sh"


def _run_backup(
    tmp_path: Path,
    *,
    fail_export: bool = False,
    fail_storage: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    tools, log = _fake_host_tools(tmp_path)
    mountpoint = tmp_path / "external"
    backups = mountpoint / "backups"
    backups.mkdir(parents=True)
    script = _stage_backup_script(tmp_path)
    environment = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "ECHO_DOCKER_BIN": str(tools / "docker"),
        "ECHO_BACKUP_DIR": str(backups),
        "ECHO_BACKUP_MOUNTPOINT": str(mountpoint),
        "ECHO_BACKUP_KEEP": "3",
        "ECHO_BACKUP_PASSPHRASE": "correct horse battery staple",
        "ECHO_MAINTENANCE_LOCK": str(tmp_path / "echo-maintenance.lock"),
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_DOCKER_FAIL_EXPORT": "1" if fail_export else "0",
        "FAKE_EXTERNAL_STORAGE_LOG": str(tmp_path / "external-storage.log"),
        "FAKE_EXTERNAL_STORAGE_FAIL": "1" if fail_storage else "0",
    }
    result = subprocess.run(
        [str(script)],
        cwd=script.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log.read_text().splitlines() if log.exists() else []


def test_verified_backup_restarts_before_retention(tmp_path) -> None:
    result, commands = _run_backup(tmp_path)

    assert result.returncode == 0, result.stderr
    stop = next(i for i, command in enumerate(commands) if " stop " in command)
    export = next(i for i, command in enumerate(commands) if " export " in command)
    verify = next(i for i, command in enumerate(commands) if " verify " in command)
    start = next(i for i, command in enumerate(commands) if " start " in command)
    prune = next(i for i, command in enumerate(commands) if " prune " in command)
    assert stop < export < verify < start < prune
    assert "--keep 3 --prefix echo-state" in commands[prune]
    assert list((tmp_path / "external" / "backups").glob("echo-state-*.echo-backup"))
    verification = (tmp_path / "external-storage.log").read_text()
    assert "--purpose state-backup" in verification
    assert f"--mountpoint {tmp_path / 'external'}" in verification


def test_failed_export_restores_previously_running_service(tmp_path) -> None:
    result, commands = _run_backup(tmp_path, fail_export=True)

    assert result.returncode != 0
    export = next(i for i, command in enumerate(commands) if " export " in command)
    start = next(i for i, command in enumerate(commands) if " start " in command)
    assert export < start
    assert not any(" prune " in command for command in commands)


def test_unverified_storage_fails_before_docker_or_service_stop(tmp_path) -> None:
    result, commands = _run_backup(tmp_path, fail_storage=True)

    assert result.returncode != 0
    assert "not a verified active external filesystem" in result.stderr
    assert commands == []
