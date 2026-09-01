"""Deterministic worktree-isolated loop, exercised against a real git repo.

Each task runs in its own ``git worktree`` off HEAD, so parallel file writes
never collide; every worktree + branch is removed afterwards and the main
checkout is left untouched.
"""

from __future__ import annotations

import shutil
import subprocess
from contextlib import suppress
from pathlib import Path

import pytest

from runtime.execution.subagents.worktree_loop import (
    is_git_repo,
    run_worktree_loop,
    shell_worktree_worker,
    subagent_worktree_worker,
    worktree_scope,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not available",
)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    g("init", "-q")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "Test")
    (repo / "README.md").write_text("base\n")
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    return repo


def _worktree_count(repo: Path) -> int:
    out = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return len([line for line in out.splitlines() if line.strip()])


def test_not_a_git_repo(tmp_path: Path):
    r = run_worktree_loop(str(tmp_path), ["t"], lambda p, t: None)
    assert r["ok"] is False
    assert "not a git repo" in r["error"]


def test_no_tasks(tmp_path: Path):
    repo = _init_repo(tmp_path)
    r = run_worktree_loop(str(repo), [], lambda p, t: None)
    assert r["ok"] is False
    assert r["error"] == "no tasks"


def test_isolated_parallel_writes(tmp_path: Path):
    repo = _init_repo(tmp_path)

    def worker(path: str, task: str) -> None:
        (Path(path) / f"{task}.txt").write_text(f"content {task}\n")

    r = run_worktree_loop(str(repo), ["alpha", "beta", "gamma"], worker)

    assert r["ok"] is True
    assert r["succeeded"] == 3
    by_index = {res["index"]: res for res in r["results"]}
    # each task's diff contains ONLY its own new file — proves isolation
    assert by_index[0]["files"] == ["alpha.txt"]
    assert by_index[1]["files"] == ["beta.txt"]
    assert "content alpha" in by_index[0]["diff"]
    assert "beta.txt" not in by_index[0]["diff"]
    # main checkout untouched + every worktree cleaned up
    assert not (repo / "alpha.txt").exists()
    assert _worktree_count(repo) == 1


def test_worker_failure_is_isolated_and_cleaned_up(tmp_path: Path):
    repo = _init_repo(tmp_path)

    def worker(path: str, task: str) -> None:
        if task == "bad":
            raise RuntimeError("boom")
        (Path(path) / f"{task}.txt").write_text("ok\n")

    r = run_worktree_loop(str(repo), ["good", "bad"], worker)

    assert r["succeeded"] == 1
    assert r["failed"] == 1
    by_index = {res["index"]: res for res in r["results"]}
    assert by_index[0]["ok"] is True
    assert by_index[1]["ok"] is False
    assert "boom" in by_index[1]["error"]
    # the failed task's worktree was still removed
    assert _worktree_count(repo) == 1


def test_shell_worktree_worker_writes_in_isolation(tmp_path: Path):
    repo = _init_repo(tmp_path)
    worker = shell_worktree_worker(
        ["sh", "-c", 'printf "%s" "$ECHO_WORKTREE_TASK" > note.txt'],
    )
    r = run_worktree_loop(str(repo), ["one", "two"], worker)

    assert r["succeeded"] == 2
    by_index = {res["index"]: res for res in r["results"]}
    # each worktree got note.txt with ITS OWN task content
    assert by_index[0]["files"] == ["note.txt"]
    assert "one" in by_index[0]["diff"]
    assert "two" in by_index[1]["diff"]
    assert "two" not in by_index[0]["diff"]
    assert _worktree_count(repo) == 1


def test_shell_worktree_worker_nonzero_exit_marks_failure(tmp_path: Path):
    repo = _init_repo(tmp_path)
    worker = shell_worktree_worker(["sh", "-c", "exit 3"])
    r = run_worktree_loop(str(repo), ["x"], worker)
    assert r["succeeded"] == 0
    assert r["results"][0]["ok"] is False
    assert _worktree_count(repo) == 1


def test_subagent_worktree_worker_passes_workspace_path(monkeypatch):
    captured: dict[str, object] = {}

    def fake(agent_id: str = "", prompt: str = "", workspace_path: str = "", **_kw):
        captured.update(
            agent_id=agent_id,
            prompt=prompt,
            workspace_path=workspace_path,
        )
        return {"success": True, "output": "ok"}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake)
    worker = subagent_worktree_worker(agent_id="worktree_writer")
    worker("/some/worktree", "do the thing")
    assert captured == {
        "agent_id": "worktree_writer",
        "prompt": "do the thing",
        "workspace_path": "/some/worktree",
    }


def test_subagent_worktree_worker_raises_on_failure(monkeypatch):
    def fake(**_kw):
        return {"success": False, "error": "boom"}

    monkeypatch.setattr("runtime.execution.subagents.call_subagent", fake)
    worker = subagent_worktree_worker()
    with pytest.raises(RuntimeError, match="boom"):
        worker("/wt", "task")


def test_worktree_scope_cleans_up_on_error(tmp_path: Path):
    repo = _init_repo(tmp_path)
    assert is_git_repo(str(repo)) is True
    with pytest.raises(RuntimeError), worktree_scope(str(repo), "boom") as (path, _branch):
        assert Path(path).is_dir()
        raise RuntimeError("x")
    assert _worktree_count(repo) == 1


def _write_fake_gitdir(gitfile: Path, fake_gitdir: Path) -> None:
    """Point a worktree's .git file at an attacker-controlled gitdir."""
    gitfile.write_text(f"gitdir: {fake_gitdir}\n", encoding="utf-8")
    (fake_gitdir / "config").write_text(
        "[core]\n\thooksPath = .\n\tfsmonitor = true\n", encoding="utf-8"
    )


def test_worktree_loop_fails_closed_on_tampered_gitfile(tmp_path: Path):
    """Audit F-03: a worker that rewrites the worktree's .git file to a
    forged gitdir must not make the trusted side load the fake config — the
    task fails closed and the main checkout stays untouched."""
    repo = _init_repo(tmp_path)

    def tamper_worker(path: str, task: str) -> None:
        fake = Path(tmp_path) / "fake-gitdir"
        fake.mkdir(exist_ok=True)
        _write_fake_gitdir(Path(path) / ".git", fake)

    r = run_worktree_loop(str(repo), ["t1"], tamper_worker)
    # The task record fails (gitfile resolves outside the main gitdir) and
    # the loop reports no success.
    assert r["ok"] is False
    assert r["succeeded"] == 0
    assert r["results"][0]["error"]
    assert "gitdir" in r["results"][0]["error"].lower()
    # The main checkout must be untouched (no diff was captured, nothing
    # applied, no worktrees left behind).
    assert _worktree_count(repo) == 1
    main_log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    # exactly the init commit — no stray commits from a fake gitdir
    assert len(main_log) == 1 and main_log[0].endswith("init")


def test_worktree_loop_rejects_gitfile_escaping_main_repo(tmp_path: Path):
    """Audit F-03: a .git file pointing OUTSIDE the main repo's git dir is
    rejected even when it is not a fake-gitdir style escape."""
    repo = _init_repo(tmp_path)

    def escape_worker(path: str, task: str) -> None:
        outside = tmp_path / "outside-git"
        outside.mkdir(exist_ok=True)
        _write_fake_gitdir(Path(path) / ".git", outside)

    r = run_worktree_loop(str(repo), ["t1"], escape_worker)
    assert r["ok"] is False
    assert r["succeeded"] == 0
    assert "gitdir" in r["results"][0]["error"].lower()


def test_worktree_loop_capture_untouched_by_hardening(tmp_path: Path):
    """Audit F-03 regression: normal worktree capture still produces the
    exact per-task diff after gitdir pinning + hardening flags."""
    repo = _init_repo(tmp_path)

    def worker(path: str, task: str) -> None:
        (Path(path) / "a.txt").write_text("hello\n", encoding="utf-8")

    r = run_worktree_loop(str(repo), ["t1"], worker)
    assert r["ok"] is True
    assert r["results"][0]["files"] == ["a.txt"]
    assert "+hello" in r["results"][0]["diff"]
    assert _worktree_count(repo) == 1


def test_oversized_diff_is_truncated_with_marker(tmp_path: Path):
    """Audit F-09: a diff larger than the cap is truncated with a marker."""
    repo = _init_repo(tmp_path)

    def worker(path: str, task: str) -> None:
        (Path(path) / "big.txt").write_text("x" * 300_000, encoding="utf-8")

    r = run_worktree_loop(str(repo), ["t1"], worker)
    assert r["ok"] is True
    diff = r["results"][0]["diff"]
    assert "diff truncated at" in diff
    assert len(diff) < 300_000


def test_symlink_in_diff_is_flagged(tmp_path: Path):
    """Audit F-09: changed symlinks are flagged in the captured diff."""
    repo = _init_repo(tmp_path)

    def worker(path: str, task: str) -> None:
        (Path(path) / "real.txt").write_text("target\n", encoding="utf-8")
        with suppress(OSError, NotImplementedError):
            (Path(path) / "link.txt").symlink_to("real.txt")

    r = run_worktree_loop(str(repo), ["t1"], worker)
    assert r["ok"] is True
    diff = r["results"][0]["diff"]
    if (Path(tmp_path) / "wt").exists():  # symlink creation supported
        assert "is a symlink" in diff

