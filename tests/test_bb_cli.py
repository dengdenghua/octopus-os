"""Cross-process blackboard CLI — real subprocesses sharing one workspace."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _bb(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "runtime", "bb", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_bb_cli_coordinates_across_processes(tmp_path: Path) -> None:
    db = str(tmp_path / "bb.db")
    base = {**os.environ, "ECHO_BLACKBOARD_DB": db, "ECHO_TURN_ID": "t1"}

    # process A writes a finding
    a = _bb(["set", "finding", "SQLi"], {**base, "ECHO_AGENT_ID": "A"})
    assert a.returncode == 0, a.stderr

    # a SEPARATE process reads it back through the shared DB file
    b = _bb(["get", "finding"], {**base, "ECHO_AGENT_ID": "B"})
    assert b.returncode == 0, b.stderr
    assert "SQLi" in b.stdout

    # a third process adds another; keys lists both
    _bb(["set", "finding2", "TOCTOU"], {**base, "ECHO_AGENT_ID": "C"})
    k = _bb(["keys"], base)
    assert "finding" in k.stdout and "finding2" in k.stdout


def test_bb_cli_requires_db_env(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "ECHO_BLACKBOARD_DB"}
    env["ECHO_TURN_ID"] = "t1"
    r = _bb(["get", "x"], env)
    assert r.returncode == 2


def test_bb_cli_missing_key_exits_1(tmp_path: Path) -> None:
    env = {**os.environ, "ECHO_BLACKBOARD_DB": str(tmp_path / "d.db"), "ECHO_TURN_ID": "t1"}
    assert _bb(["get", "absent"], env).returncode == 1

