from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.reflex.git")

DEFAULT_AUTHOR = ("Echo Reflex", "reflex@echo.local")


def is_git_repo(path: Path) -> bool:
    """Return True if ``path`` is inside a git repo (or is one)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path.parent if path.is_file() else path,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return out.returncode == 0 and out.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command · returns (returncode, stdout, stderr)."""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def has_changes(file_path: Path) -> bool:
    """Return True when ``file_path`` has unstaged or staged changes."""
    rel = file_path.name
    cwd = file_path.parent
    code, out, _ = _git(["status", "--porcelain", "--", rel], cwd=cwd)
    return code == 0 and out.strip() != ""


def auto_commit(
    file_path: Path,
    *,
    diff_summary: str = "",
    author_name: str | None = None,
    author_email: str | None = None,
) -> dict[str, Any]:
    if not file_path.is_file():
        return {"ok": False, "error": f"file not found: {file_path}"}
    cwd = file_path.parent
    if not is_git_repo(cwd):
        return {"ok": False, "skipped": True, "reason": "not a git repo"}
    if not has_changes(file_path):
        return {"ok": True, "skipped": True, "reason": "no changes"}

    name = author_name or DEFAULT_AUTHOR[0]
    email = author_email or DEFAULT_AUTHOR[1]
    rel = file_path.name
    msg = "reflex reload"
    if diff_summary:
        msg = f"reflex reload · {diff_summary}"

    code, _, err = _git(["add", "--", rel], cwd=cwd)
    if code != 0:
        return {"ok": False, "error": f"git add failed: {err.strip()}"}

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = name
    env["GIT_AUTHOR_EMAIL"] = email
    env["GIT_COMMITTER_NAME"] = name
    env["GIT_COMMITTER_EMAIL"] = email
    proc = subprocess.run(
        ["git", "commit", "-m", msg, "--", rel],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": f"git commit: {proc.stderr.strip()}"}

    # Return short SHA for the response.
    code, sha, _ = _git(["rev-parse", "--short", "HEAD"], cwd=cwd)
    return {
        "ok": True,
        "message": msg,
        "sha": sha.strip() if code == 0 else None,
    }


def file_history(
    file_path: Path,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return the most recent commits touching ``file_path``."""
    if not file_path.is_file():
        return []
    cwd = file_path.parent
    if not is_git_repo(cwd):
        return []
    fmt = "%H|%h|%an|%ae|%at|%s"
    code, out, _ = _git(
        ["log", f"-n{int(limit)}", f"--pretty=format:{fmt}", "--", file_path.name],
        cwd=cwd,
    )
    if code != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 5)
        if len(parts) < 6:
            continue
        rows.append(
            {
                "sha_full": parts[0],
                "sha": parts[1],
                "author_name": parts[2],
                "author_email": parts[3],
                "ts": int(parts[4]) if parts[4].isdigit() else None,
                "subject": parts[5],
            }
        )
    return rows


def format_diff_summary(diff: dict[str, Any]) -> str:
    """Build the one-line message body from a reload diff dict ·
    e.g. ``+weather_query +ac_set_temp -hotreload_test ~thanks``.
    Modified rules show as ``~name`` regardless of which field
    changed · the full before/after dict lives in last-reload's
    response if anyone needs the detail."""
    parts: list[str] = []
    for rid in diff.get("added") or []:
        parts.append(f"+{rid}")
    for rid in diff.get("removed") or []:
        parts.append(f"-{rid}")
    mod = diff.get("modified") or []
    # ``modified`` is a list of strings on the inbound diff dict ·
    # the response dict has a richer list of dicts. Handle both.
    for m in mod:
        if isinstance(m, dict):
            parts.append(f"~{m.get('rule_id', '?')}")
        else:
            parts.append(f"~{m}")
    return " ".join(parts) or "(no rule-id changes detected)"


__all__ = [
    "is_git_repo",
    "has_changes",
    "auto_commit",
    "file_history",
    "format_diff_summary",
]
