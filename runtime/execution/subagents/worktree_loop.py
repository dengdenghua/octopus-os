"""Deterministic worktree-isolated loop.

Run N tasks, each in its OWN git worktree, concurrently — capture each one's
diff, then clean up. Isolated parallel file-writing: workers never collide
because each operates in a separate checkout off ``HEAD``.

This is the real mechanism behind the worktree pattern the
``vibecoding-general-swarm`` SKILL only described in prose (telling sub-agents
to manually shell ``git worktree add``). Here the lifecycle is code:
deterministic, cleaned up in a ``finally``, and unit-tested against a real git
repo.

Diffs are RETURNED for the caller to review/apply — this never auto-merges,
because reconciling parallel edits to the same file is a human/lead decision,
not something to do blindly.

The ``worker`` is an injected callable ``worker(worktree_path, task) -> None``
that writes files inside the worktree. Wiring a sub-agent as the worker (so it
runs with ``cwd`` = the worktree) needs per-worker ``workspace_path`` support
in ``call_subagent`` and is a separate integration step; the loop machinery
here is agnostic to what the worker is.
"""

from __future__ import annotations

import concurrent.futures as _cf
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

# ``git worktree add/remove`` mutate the main repo's worktree registry, so
# those are serialized. The worker and the per-worktree diff capture touch only
# the worktree's OWN index/checkout, so they run concurrently without a lock.
_WORKTREE_LOCK = threading.Lock()
_MAX_WORKTREE_TASKS = 16


def _git(cwd: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
        check=check,
    )


# Audit F-03: a worktree's ``.git`` is a *file* pointing at the gitdir, and it
# sits inside the worktree — inside a confined worker's write root. If a
# worker (or a prompt-injected one) rewrites that file to point at an
# attacker-controlled gitdir, a naive ``git -C <wt>`` would load config and
# hooks from the fake gitdir (fsmonitor, hooksPath, include.path, diff
# drivers) and execute them on the trusted side. Every per-worktree git call
# therefore pins the gitdir/worktree explicitly and hardens the command:
#   * --git-dir / --work-tree resolve the exact validated gitdir, so the
#     worktree's ``.git`` file is never consulted again.
#   * core.fsmonitor=false + core.hooksPath= neutralise the two classic
#     auto-exec hooks; core.attributesFile= drops the shared attributes file.
#   * --no-textconv on diff so a malicious in-worktree .gitattributes cannot
#     trigger a configured textconv driver during capture.
_GIT_HARDENING: tuple[str, ...] = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=",
    "-c",
    "core.attributesFile=",
)


def _resolve_worktree_gitdir(worktree: str, repo_root: str) -> str:
    """Resolve and validate the gitdir a worktree's ``.git`` file points at.

    Fail closed (audit F-03): the resolved gitdir must live inside the main
    repo's git-common-dir; anything else (tampered ``.git`` file) raises so
    the caller marks the task failed instead of running git against an
    attacker-controlled repository.
    """
    gitfile = os.path.join(worktree, ".git")
    try:
        with open(gitfile, encoding="utf-8") as fh:
            line = fh.read().strip()
    except OSError as exc:
        raise RuntimeError(f"worktree .git unreadable: {exc}") from exc
    if not line.startswith("gitdir:"):
        raise RuntimeError(f"worktree .git is not a gitdir pointer: {line[:80]!r}")
    raw = line.split(":", 1)[1].strip()
    gitdir = raw if os.path.isabs(raw) else os.path.normpath(os.path.join(worktree, raw))
    # The main repo is trusted (its .git is the real one); the worktree gitdir
    # must be nested inside it. Use --git-common-dir so linked-worktree repos
    # (where repo_root's own .git is a gitfile too) still resolve to the real
    # shared gitdir.
    common = _git(repo_root, "rev-parse", "--git-common-dir", check=False)
    if common.returncode != 0:
        raise RuntimeError(f"cannot resolve main gitdir: {common.stderr.strip()}")
    common_root = common.stdout.strip()
    if not os.path.isabs(common_root):
        common_root = os.path.normpath(os.path.join(repo_root, common_root))
    try:
        real_gitdir = os.path.realpath(gitdir)
        real_common = os.path.realpath(common_root)
        inside = os.path.commonpath([real_gitdir, real_common]) == real_common
    except ValueError:
        inside = False
    if not inside:
        raise RuntimeError(f"worktree gitdir escapes main repo: {gitdir}")
    return gitdir


def _git_in_worktree(
    worktree: str,
    repo_root: str,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run git against a worktree with a pinned, validated gitdir (F-03)."""
    gitdir = _resolve_worktree_gitdir(worktree, repo_root)
    return subprocess.run(
        [
            "git",
            f"--git-dir={gitdir}",
            f"--work-tree={worktree}",
            *_GIT_HARDENING,
            *args,
        ],
        capture_output=True,
        text=True,
        check=check,
    )


def is_git_repo(path: str) -> bool:
    try:
        result = _git(path, "rev-parse", "--is-inside-work-tree", check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _slug(text: str, fallback: str) -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in str(text))[:32].strip(
        "-"
    )
    return cleaned or fallback


def _restore_worktree_gitfile(worktree: str, expected_gitdir: str) -> None:
    """Rewrite a worktree's ``.git`` file back to the gitdir we created it
    with (audit F-03). A worker may have tampered the pointer; ``git worktree
    remove`` refuses to remove a worktree whose gitdir no longer matches the
    registry, so we restore the invariant we established at add time before
    tearing the worktree down. Best-effort: cleanup must never raise."""
    if not expected_gitdir:
        return
    gitfile = os.path.join(worktree, ".git")
    try:
        with open(gitfile, encoding="utf-8") as fh:
            line = fh.read().strip()
    except OSError:
        return
    if line == f"gitdir: {expected_gitdir}":
        return
    try:
        with open(gitfile, "w", encoding="utf-8") as fh:
            fh.write(f"gitdir: {expected_gitdir}\n")
    except OSError:  # noqa: BLE001 — best-effort cleanup must never raise
        pass


@contextmanager
def worktree_scope(repo_root: str, name: str) -> Iterator[tuple[str, str]]:
    """Create an isolated git worktree off HEAD, yield ``(path, branch)``, and
    remove the worktree + branch on exit (always, even on error)."""
    base = tempfile.mkdtemp(prefix="octo-wt-")
    path = os.path.join(base, "wt")
    branch = f"octo/wt-{name}"
    gitdir = ""
    with _WORKTREE_LOCK:
        _git(repo_root, "worktree", "add", "-b", branch, path, "HEAD")
        # Record the gitdir the worktree was registered with while the .git
        # pointer is still pristine — cleanup restores it if a worker tampers
        # with the file (audit F-03). git auto-uniquifies same-basename
        # worktrees (wt, wt1, ...), so the admin dir name is not predictable;
        # the pointer is the source of truth.
        try:
            with open(os.path.join(path, ".git"), encoding="utf-8") as fh:
                gitdir = fh.read().strip().split(":", 1)[1].strip()
        except OSError:
            gitdir = ""
    try:
        yield path, branch
    finally:
        with _WORKTREE_LOCK:
            _restore_worktree_gitfile(path, gitdir)
            _git(repo_root, "worktree", "remove", "--force", path, check=False)
            _git(repo_root, "worktree", "prune", check=False)
            _git(repo_root, "branch", "-D", branch, check=False)
        shutil.rmtree(base, ignore_errors=True)


# Audit F-09: a single oversized diff (e.g. a committed binary or generated
# blob) must not blow up the model context; the diff is capped and truncated
# with an explicit marker.
_MAX_DIFF_CHARS = 200_000


def _cap_diff(diff: str) -> str:
    if len(diff) <= _MAX_DIFF_CHARS:
        return diff
    return diff[:_MAX_DIFF_CHARS] + f"\n...(diff truncated at {_MAX_DIFF_CHARS} chars)\n"


def _symlink_warnings(worktree: str, files: list[str]) -> str:
    """Flag changed paths that are symlinks (audit F-09) so a caller that
    later applies the diff knows a link target is involved."""
    warnings: list[str] = []
    for rel in files:
        path = os.path.join(worktree, rel)
        try:
            if os.path.islink(path):
                warnings.append(f"warning: {rel} is a symlink -> {os.readlink(path)}")
        except OSError:
            continue
    return "\n".join(warnings)


def _capture_diff(worktree: str, repo_root: str) -> tuple[str, list[str]]:
    # Fail closed: if the worktree's .git file was tampered with, resolving it
    # raises and the task is marked failed rather than running git against a
    # forged gitdir (audit F-03).
    _git_in_worktree(worktree, repo_root, "add", "-A", check=False)
    diff = _git_in_worktree(
        worktree, repo_root, "diff", "--cached", "--no-textconv", check=False
    ).stdout
    names = _git_in_worktree(
        worktree, repo_root, "diff", "--cached", "--name-only", "--no-textconv", check=False
    ).stdout
    files = [line for line in names.split("\n") if line.strip()]
    diff = _cap_diff(diff)
    warnings = _symlink_warnings(worktree, files)
    if warnings:
        diff = (warnings + "\n" + diff) if diff else warnings
    return diff, files


def shell_worktree_worker(
    command: list[str],
    *,
    timeout_s: int = 300,
) -> Callable[[str, Any], None]:
    """A ready-made worker that runs a FIXED argv inside each worktree
    (``cwd`` = the worktree), with the task exposed as the env var
    ``$ECHO_WORKTREE_TASK``. The task string is never interpolated into the
    command and no shell is spawned by us, so an untrusted task can't inject
    argv. A non-zero exit raises (the loop marks that task failed). This is the
    works-today consumer — running a sub-agent as the worker (so the agent's
    own file tools target the worktree) needs per-worker write-scope wiring in
    the executor and is deferred until it can be verified against a live run."""
    argv = [str(part) for part in command]

    def _worker(path: str, task: Any) -> None:
        env = {**os.environ, "ECHO_WORKTREE_TASK": str(task)}
        from runtime.platform.process.tree import run_capture

        result = run_capture(
            argv,
            cwd=path,
            env=env,
            timeout=timeout_s,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                argv,
                output=result.stdout,
                stderr=result.stderr,
            )

    return _worker


def subagent_worktree_worker(
    agent_id: str = "worktree_writer",
    *,
    timeout_s: int = 600,
) -> Callable[[str, Any], None]:
    """A worker that runs an LLM sub-agent inside each worktree, with the
    sub-agent's OWN file tools confined to that worktree.

    Confinement goes through ``call_subagent(workspace_path=...)``: the locked
    worktree is pinned on the sub-agent's Session and the ephemeral chokepoint
    injects it as ``sandbox_dir`` for every write skill, so the sub-agent can
    write ONLY inside its worktree (verified live with an escape test). The
    default role ``worktree_writer`` has no shell — a shell would bypass the
    sandbox_dir confinement. A failed run raises so the loop marks it failed."""

    def _worker(path: str, task: Any) -> None:
        from runtime.execution.subagents import call_subagent

        result = call_subagent(
            agent_id=agent_id,
            prompt=str(task),
            workspace_path=path,
            timeout_s=timeout_s,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "subagent failed")

    return _worker


def run_worktree_loop(
    repo_root: str,
    tasks: list[Any],
    worker: Callable[[str, Any], None],
    *,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Run each task in its own git worktree concurrently; return per-task
    ``{index, task, branch, ok, diff, files, error}``. Never auto-merges."""
    if not is_git_repo(repo_root):
        return {"ok": False, "error": f"not a git repo: {repo_root}", "results": [], "count": 0}
    clean = [t for t in (tasks or []) if t is not None][:_MAX_WORKTREE_TASKS]
    if not clean:
        return {"ok": False, "error": "no tasks", "results": [], "count": 0}

    def _run_one(index: int, task: Any) -> dict[str, Any]:
        preview = (task if isinstance(task, str) else repr(task))[:200]
        name = f"{index}-{_slug(task if isinstance(task, str) else '', f't{index}')}"
        record: dict[str, Any] = {
            "index": index,
            "task": preview,
            "branch": f"octo/wt-{name}",
            "ok": False,
            "diff": "",
            "files": [],
            "error": None,
        }
        try:
            with worktree_scope(repo_root, name) as (path, branch):
                record["branch"] = branch
                worker(path, task)
                record["diff"], record["files"] = _capture_diff(path, repo_root)
                record["ok"] = True
        except Exception as exc:  # noqa: BLE001 — isolate one task's failure
            record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    results: list[dict[str, Any]] = []
    workers = max(1, min(int(max_workers), len(clean)))
    with _cf.ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="worktree",
    ) as pool:
        futures = [pool.submit(_run_one, i, t) for i, t in enumerate(clean)]
        for future in _cf.as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda r: r["index"])
    succeeded = sum(1 for r in results if r["ok"])
    return {
        "ok": succeeded > 0,
        "results": results,
        "count": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
    }
