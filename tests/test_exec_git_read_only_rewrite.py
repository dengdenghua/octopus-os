"""Regression: read-only ``git`` via exec_shell may inspect the workspace root
in sandbox mode.

exec_shell is classified as a mutating tool, so in sandbox mode its explicit
``cwd`` is confined to the sandbox workdir and a ``git status`` aimed at the
workspace root fails with ``path_escapes_sandbox``. The git READ skills
(git_status/git_diff/git_log) already run at the workspace root — their
affinity is ``read``. A raw read-only ``git`` command should get the same
treatment: rewrite it to ``git -C <root> …`` so the process stays
sandbox-confined while git inspects the requested root.

Misclassification is fail-safe: unknown/mutating subcommands are left alone.
"""

from __future__ import annotations

import pytest

from runtime.execution.suckers._write_skills_exec import (
    _exec_shell,
    _is_read_only_git_argv,
    _read_only_git_rewrite,
)

_REPO = "/workspace/repo"
_WORK = "/workspace/repo/.echo-work/t1"


def test_is_read_only_git_status() -> None:
    assert _is_read_only_git_argv(["git", "status", "--porcelain"]) is True


def test_is_read_only_git_diff_log_show() -> None:
    assert _is_read_only_git_argv(["git", "diff"]) is True
    assert _is_read_only_git_argv(["git", "log", "--oneline", "-5"]) is True
    assert _is_read_only_git_argv(["git", "show", "HEAD"]) is True


def test_is_read_only_git_tolerates_global_options() -> None:
    assert _is_read_only_git_argv(["git", "-C", _REPO, "status"]) is True
    assert _is_read_only_git_argv(["git", "-c", "core.pager=cat", "diff"]) is True
    assert _is_read_only_git_argv(["git", "--no-pager", "log", "-1"]) is True


def test_mutating_git_is_not_read_only() -> None:
    assert _is_read_only_git_argv(["git", "add", "."]) is False
    assert _is_read_only_git_argv(["git", "commit", "-m", "x"]) is False
    assert _is_read_only_git_argv(["git", "checkout", "."]) is False


def test_unknown_subcommand_is_fail_closed() -> None:
    assert _is_read_only_git_argv(["git", "st"]) is False


def test_non_git_argv_is_not_read_only() -> None:
    assert _is_read_only_git_argv(["ls", "-la"]) is False
    assert _is_read_only_git_argv(["python", "-c", "pass"]) is False


def test_rewrite_git_status_at_workspace_root() -> None:
    rewritten = _read_only_git_rewrite(["git", "status", "--porcelain"], _REPO, _WORK)
    assert rewritten is not None
    argv, cwd = rewritten
    assert argv == ["git", "-C", _REPO, "status", "--porcelain"]
    assert cwd is None  # process stays at the sandbox root


def test_rewrite_keeps_global_options() -> None:
    rewritten = _read_only_git_rewrite(["git", "--no-pager", "diff"], _REPO, _WORK)
    assert rewritten is not None
    argv, _ = rewritten
    assert argv == ["git", "-C", _REPO, "--no-pager", "diff"]


def test_no_rewrite_when_cwd_inside_sandbox() -> None:
    inside = _WORK + "/sub"
    assert _read_only_git_rewrite(["git", "status"], inside, _WORK) is None


def test_no_rewrite_without_cwd_or_sandbox() -> None:
    assert _read_only_git_rewrite(["git", "status"], None, _WORK) is None
    assert _read_only_git_rewrite(["git", "status"], _REPO, None) is None


def test_no_rewrite_for_mutating_git() -> None:
    assert _read_only_git_rewrite(["git", "add", "."], _REPO, _WORK) is None


def _stream_ok(**kwargs):
    captured = {}

    def _fake(*args, **kw):
        captured.update(argv=args[0], cwd=kw.get("cwd"), sandbox_dir=kw.get("sandbox_dir"))
        return {
            "exit_code": 0,
            "stdout": " M other.py\n",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    return _fake, captured


def test_exec_shell_rewrites_read_only_git_at_workspace_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    work = tmp_path / ".echo-work" / "t1"
    work.mkdir(parents=True)
    fake, captured = _stream_ok()
    monkeypatch.setattr("runtime.platform.process.streaming.stream_run", fake)

    result = _exec_shell(
        "git status --porcelain",
        cwd=str(tmp_path),
        sandbox_dir=str(work),
    )

    assert "error" not in result
    # Rewritten to git -C <repo-root> status; process cwd stays at the
    # sandbox root so the sandbox backend keeps write confinement.
    assert captured["argv"] == ["git", "-C", str(tmp_path), "status", "--porcelain"]
    assert captured["cwd"] == str(work.resolve())
    assert captured["sandbox_dir"] == str(work)


def test_exec_shell_mutating_git_stays_confined(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    work = tmp_path / ".echo-work" / "t1"
    work.mkdir(parents=True)
    fake, _ = _stream_ok()
    monkeypatch.setattr("runtime.platform.process.streaming.stream_run", fake)

    # git add is not read-only → no rewrite → cwd outside the sandbox fails.
    result = _exec_shell("git add .", cwd=str(tmp_path), sandbox_dir=str(work))
    assert "path_escapes_sandbox" in result.get("error", "")

