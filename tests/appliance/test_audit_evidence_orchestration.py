"""Host audit export restores service state before retention."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _stage_export_script(tmp_path: Path) -> Path:
    source = Path(__file__).parents[2] / "deploy" / "appliance"
    staged = tmp_path / "staged"
    staged.mkdir()
    shutil.copy2(source / "export-audit-evidence.sh", staged / "export-audit-evidence.sh")
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
    return staged / "export-audit-evidence.sh"


def _run(tmp_path: Path, *, fail_export: bool = False, fail_storage: bool = False):
    tools = tmp_path / "tools"
    evidence = tmp_path / "external-evidence"
    tools.mkdir()
    evidence.mkdir()
    log = tmp_path / "docker.log"
    docker = tools / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
case " $* " in
  *" ps --status running --services "*) printf 'echo-os\n' ;;
  *" appliance.audit_evidence export "*)
    [[ "${FAKE_DOCKER_FAIL_EXPORT:-0}" != "1" ]] || exit 42
    for argument in "$@"; do
      case "$argument" in
        /evidence/*.echo-audit)
          : > "$ECHO_AUDIT_EXPORT_DIR/${argument##*/}"
          chmod 600 "$ECHO_AUDIT_EXPORT_DIR/${argument##*/}"
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
    script = _stage_export_script(tmp_path)
    result = subprocess.run(
        [str(script)],
        cwd=script.parent,
        env={
            **os.environ,
            "PATH": f"{tools}:{os.environ['PATH']}",
            "ECHO_DOCKER_BIN": str(docker),
            "ECHO_AUDIT_EXPORT_DIR": str(evidence),
            "ECHO_AUDIT_EXPORT_MOUNTPOINT": str(evidence),
            "ECHO_AUDIT_KEEP_DAYS": "180",
            "ECHO_AUDIT_KEEP_MINIMUM": "6",
            "ECHO_AUDIT_EXPORT_PASSPHRASE": "correct horse battery staple",
            "ECHO_MAINTENANCE_LOCK": str(tmp_path / "echo-maintenance.lock"),
            "FAKE_DOCKER_LOG": str(log),
            "FAKE_DOCKER_FAIL_EXPORT": "1" if fail_export else "0",
            "FAKE_EXTERNAL_STORAGE_LOG": str(tmp_path / "external-storage.log"),
            "FAKE_EXTERNAL_STORAGE_FAIL": "1" if fail_storage else "0",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log.read_text().splitlines() if log.exists() else [], evidence


def test_export_verifies_restarts_then_applies_retention(tmp_path) -> None:
    result, commands, evidence = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    positions = {
        marker: next(i for i, command in enumerate(commands) if marker in command)
        for marker in (" stop ", " export ", " verify ", " start ", " prune ")
    }
    assert (
        positions[" stop "]
        < positions[" export "]
        < positions[" verify "]
        < positions[" start "]
        < positions[" prune "]
    )
    assert "--keep-days 180 --keep-minimum 6" in commands[positions[" prune "]]
    assert list(evidence.glob("echo-audit-*.echo-audit"))
    verification = (tmp_path / "external-storage.log").read_text()
    assert "--purpose audit-evidence" in verification
    assert f"--mountpoint {evidence}" in verification


def test_failed_export_restarts_and_never_runs_retention(tmp_path) -> None:
    result, commands, _evidence = _run(tmp_path, fail_export=True)

    assert result.returncode != 0
    export = next(i for i, command in enumerate(commands) if " export " in command)
    start = next(i for i, command in enumerate(commands) if " start " in command)
    assert export < start
    assert not any(" prune " in command for command in commands)


def test_unverified_storage_fails_before_docker_or_service_stop(tmp_path) -> None:
    result, commands, _evidence = _run(tmp_path, fail_storage=True)

    assert result.returncode != 0
    assert "not a verified active external filesystem" in result.stderr
    assert commands == []
