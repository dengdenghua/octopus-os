"""Git network / branch-switch skills for write_skills · extracted from
write_skills.py.

Contains the opt-in, dangerous git skills: push / pull / checkout / stash /
create_pr.
"""

from __future__ import annotations

from typing import Any

from ._write_skills_common import (
    _ensure_sandbox,
    _error_with_execution_policy,
    _execution_policy_from_result,
)
from ._write_skills_git import _GIT_WRITE_TIMEOUT_S, _run_git


def _git_push(
    repo_dir: str = "",
    *,
    remote: str = "origin",
    branch: str | None = None,
    set_upstream: bool = False,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Push commits to remote. Never force-pushes."""
    if remote.startswith("-"):
        return {"error": f"invalid remote: {remote!r}"}
    argv = ["push", remote]
    if branch:
        if branch.startswith("-"):
            return {"error": f"invalid branch: {branch!r}"}
        argv.append(branch)
    if set_upstream:
        argv.insert(1, "-u")
    r = _run_git(
        repo_dir,
        argv,
        timeout_s=60.0,
        sandbox_dir=sandbox_dir,
        allow_network=True,
    )
    if "error" in r:
        return r
    if r["exit_code"] != 0:
        return {"error": "git_push_failed", **r}
    return {"pushed": True, "remote": remote, "branch": branch}


def _git_pull(
    repo_dir: str = "",
    *,
    remote: str = "origin",
    branch: str | None = None,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Pull from remote (rebase mode)."""
    if remote.startswith("-"):
        return {"error": f"invalid remote: {remote!r}"}
    argv = ["pull", "--rebase", remote]
    if branch:
        if branch.startswith("-"):
            return {"error": f"invalid branch: {branch!r}"}
        argv.append(branch)
    r = _run_git(
        repo_dir,
        argv,
        timeout_s=60.0,
        sandbox_dir=sandbox_dir,
        allow_network=True,
    )
    if "error" in r:
        return r
    if r["exit_code"] != 0:
        return {"error": "git_pull_failed", **r}
    return {"pulled": True, "remote": remote, "branch": branch, "stdout": r["stdout"][:2000]}


def _git_checkout(
    repo_dir: str = "",
    *,
    branch: str = "",
    create: bool = False,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Switch branch. Auto-stashes dirty state."""
    if not branch or branch.startswith("-"):
        return {"error": f"invalid branch: {branch!r}"}
    stash_r = _run_git(
        repo_dir, ["stash", "--include-untracked"], timeout_s=15.0, sandbox_dir=sandbox_dir
    )
    stashed = stash_r.get("exit_code") == 0 and "No local changes" not in stash_r.get("stdout", "")
    argv = ["checkout"]
    if create:
        argv.append("-b")
    argv.append(branch)
    r = _run_git(repo_dir, argv, timeout_s=_GIT_WRITE_TIMEOUT_S, sandbox_dir=sandbox_dir)
    if "error" in r:
        if stashed:
            _run_git(repo_dir, ["stash", "pop"], timeout_s=15.0, sandbox_dir=sandbox_dir)
        return r
    if r["exit_code"] != 0:
        if stashed:
            _run_git(repo_dir, ["stash", "pop"], timeout_s=15.0, sandbox_dir=sandbox_dir)
        return {"error": "git_checkout_failed", **r}
    result: dict[str, Any] = {"switched": branch, "created": create}
    if stashed:
        pop_r = _run_git(repo_dir, ["stash", "pop"], timeout_s=15.0, sandbox_dir=sandbox_dir)
        result["stash_restored"] = pop_r.get("exit_code") == 0
    return result


def _git_stash(
    repo_dir: str = "",
    *,
    action: str = "push",
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Stash push/pop/list."""
    allowed = {"push", "pop", "list", "show", "drop"}
    if action not in allowed:
        return {"error": f"invalid action: {action!r}, allowed: {allowed}"}
    argv = ["stash", action]
    if action == "push":
        argv.append("--include-untracked")
    r = _run_git(repo_dir, argv, timeout_s=15.0, sandbox_dir=sandbox_dir)
    if "error" in r:
        return r
    if r["exit_code"] != 0:
        return {"error": f"git_stash_{action}_failed", **r}
    return {"action": action, "stdout": r["stdout"][:4000]}


def _git_create_pr(
    repo_dir: str = "",
    *,
    title: str = "",
    body: str = "",
    base: str | None = None,
    draft: bool = False,
    sandbox_dir: str | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    """Create a GitHub PR using the `gh` CLI."""
    if not title:
        return {"error": "title is required"}

    resolved, err = _ensure_sandbox(repo_dir, sandbox_dir)
    if err:
        return {"error": err}
    argv = ["gh", "pr", "create", "--title", title]
    if body:
        argv.extend(["--body", body])
    if base:
        argv.extend(["--base", base])
    if draft:
        argv.append("--draft")

    from runtime.platform.process.streaming import stream_run

    r = stream_run(
        argv,
        cwd=str(resolved),
        timeout=30.0,
        output_cap_bytes=8000,
        sandbox_dir=sandbox_dir,
        allow_network=True,
        sandbox_required=True,
    )
    if "error" in r and "exit_code" not in r:
        msg = r["error"]
        if "FileNotFoundError" in msg or "not found" in msg.lower():
            return _error_with_execution_policy(
                "gh CLI not found — install from https://cli.github.com",
                r,
            )
        return _error_with_execution_policy(f"gh_exec_failed: {msg}", r)
    if r.get("timed_out"):
        return {
            "error": "timeout creating PR",
            "execution_policy": _execution_policy_from_result(r),
        }
    if r["exit_code"] != 0:
        return {
            "error": "gh_pr_create_failed",
            "stderr": r["stderr"][:2000],
            "exit_code": r["exit_code"],
            "execution_policy": _execution_policy_from_result(r),
        }
    return {
        "created": True,
        "url": r["stdout"].strip(),
        "sandbox_backend": r.get("sandbox_backend", "direct"),
        "sandbox_hard": bool(r.get("sandbox_hard")),
        "execution_policy": _execution_policy_from_result(r),
    }
