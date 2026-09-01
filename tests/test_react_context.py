from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from runtime.core.cerebrum.react_context import _git_status_summary


def test_git_status_summary_reports_branch_counts(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[-2:] == ["branch", "--show-current"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="feature/agent\n", stderr="")
        if cmd[-2:] == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=" M runtime/foo.py\n?? tests/test_foo.py\nA  new.py\n",
                stderr="",
            )
        if "--left-right" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="2\t1\n", stderr="")
        if cmd[-2:] == ["-1", "--pretty=format:%h %s"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="abc1234 fix: agent guard\n",
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    summary = _git_status_summary(str(tmp_path))

    assert (
        summary
        == 'branch=feature/agent modified=2 untracked=1 ahead=1 behind=2 last="abc1234 fix: agent guard"'
    )
    assert calls


def test_git_status_summary_returns_empty_outside_repo(monkeypatch: Any, tmp_path: Path) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="not a git repo")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _git_status_summary(str(tmp_path)) == ""
