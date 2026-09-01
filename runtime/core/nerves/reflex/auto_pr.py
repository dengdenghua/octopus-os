"""
Auto-PR generator · turn accumulated suggestions into a real git
branch + commit + (optionally) a GitHub PR via the ``gh`` CLI.

Why
---

The suggestions endpoint already produces ready-to-paste YAML
snippets. The remaining manual steps · open editor, paste, save,
``git add``, ``git commit``, ``git push``, ``gh pr create`` · are
exactly the kind of friction that keeps operators on "I'll do it
later" mode forever. This module collapses them into one POST.

Workflow
--------

1. POST /api/reflex/auto-pr · the server:
   a. fetches current top-N suggestions (clustered)
   b. opens ``data/reflex_rules.yaml`` and appends the new rules
      under a clearly delimited "auto-suggested" section
   c. ``git checkout -b reflex/auto-YYYY-MM-DD-HHMM``
   d. ``git add data/reflex_rules.yaml && git commit``
   e. (optional) ``git push origin <branch>``
   f. (optional) ``gh pr create`` with body listing the prompts +
      counts that motivated each rule
2. Returns the branch name + PR URL (when ``gh`` was used)

Design rules
------------

* **Reply text is left as TODO** · the operator must still pick
  the right reply for each new rule before merging. We refuse to
  invent it · wrong canned reply is worse than the original
  LLM-roundtrip baseline.
* **Idempotent enough**: if the branch already exists, append to
  it instead of failing. If suggestions are unchanged since last
  run, no-op.
* **Never push without explicit opt-in**: ``push=true`` and
  ``open_pr=true`` are separate flags · default is "make the
  branch + commit locally, let me review before pushing".
"""

from __future__ import annotations

import datetime as _dt
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.reflex.auto_pr")

_AUTO_BLOCK_HEADER = "# ─── auto-suggested rules · review then deploy ───"


def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _branch_name() -> str:
    """Time-stamped branch name · stable enough that operators can
    track multiple in-flight PRs but unique enough to never collide
    with a re-run from the same minute."""
    ts = _dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    return f"reflex/auto-{ts}"


def _append_rules(file_path: Path, snippets: list[str]) -> str:
    """Append the suggested-rule snippets under a delimited section
    so the operator can find + edit them fast. Returns the new file
    contents (the caller persists it)."""
    text = file_path.read_text(encoding="utf-8")
    block_lines: list[str] = []
    if _AUTO_BLOCK_HEADER not in text:
        block_lines.append("")
        block_lines.append(_AUTO_BLOCK_HEADER)
    for s in snippets:
        block_lines.append(s.rstrip("\n"))
    new_block = "\n".join(block_lines) + "\n"
    if not text.endswith("\n"):
        text += "\n"
    return text + new_block


def generate_pr(
    *,
    file_path: Path,
    suggestions: list[dict[str, Any]],
    push: bool = False,
    open_pr: bool = False,
    base_branch: str = "main",
) -> dict[str, Any]:
    if not file_path.is_file():
        return {"ok": False, "error": f"file not found: {file_path}"}
    cwd = file_path.parent
    code, _, _ = _git(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    if code != 0:
        return {"ok": False, "error": "not a git repo"}
    if not suggestions:
        return {"ok": False, "error": "no suggestions to apply"}

    # Capture current branch so we can return to it on failure.
    code, current_branch_raw, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    current_branch: str | None = current_branch_raw.strip() if code == 0 else None
    branch = _branch_name()

    # Create branch from current HEAD (don't depend on `main` existing
    # · operator may use master / a working branch).
    code, _, err = _git(["checkout", "-b", branch], cwd=cwd)
    if code != 0:
        return {
            "ok": False,
            "error": f"checkout -b {branch} failed: {err.strip()}",
        }

    try:
        # Append snippets to the file.
        snippets = [s.get("suggested_yaml", "") for s in suggestions if s.get("suggested_yaml")]
        new_text = _append_rules(file_path, snippets)
        file_path.write_text(new_text, encoding="utf-8")

        # Commit.
        rel = file_path.name
        _git(["add", "--", rel], cwd=cwd)
        bullets = "\n".join(
            f"- {s.get('prompt', '?')!r} (×{s.get('count', 0)})" for s in suggestions[:10]
        )
        msg = (
            f"reflex: auto-suggest {len(suggestions)} rule(s)\n\n"
            f"Top hits:\n{bullets}\n\n"
            "Reply text is TODO · review and replace before merging."
        )
        proc = subprocess.run(
            ["git", "commit", "-m", msg, "--", rel],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            # Roll back the branch on commit failure.
            if current_branch:
                _git(["checkout", current_branch], cwd=cwd)
            _git(["branch", "-D", branch], cwd=cwd)
            return {
                "ok": False,
                "error": f"git commit: {proc.stderr.strip()}",
            }
        code, sha_raw, _ = _git(["rev-parse", "--short", "HEAD"], cwd=cwd)
        sha: str | None = sha_raw.strip() if code == 0 else None

        result: dict[str, Any] = {
            "ok": True,
            "branch": branch,
            "file": str(file_path),
            "sha": sha,
            "rules_added": len(snippets),
        }

        if push:
            push_code, _, push_err = _git(
                ["push", "-u", "origin", branch],
                cwd=cwd,
            )
            if push_code != 0:
                result["push_error"] = push_err.strip()
            else:
                result["pushed"] = True
                if open_pr:
                    if shutil.which("gh") is None:
                        result["pr_error"] = "gh CLI not installed"
                    else:
                        proc = subprocess.run(
                            [
                                "gh",
                                "pr",
                                "create",
                                "--base",
                                base_branch,
                                "--head",
                                branch,
                                "--title",
                                f"reflex: auto-suggest {len(snippets)} rule(s)",
                                "--body",
                                msg,
                            ],
                            cwd=cwd,
                            capture_output=True,
                            text=True,
                            timeout=20,
                        )
                        if proc.returncode == 0:
                            result["pr_url"] = proc.stdout.strip()
                        else:
                            result["pr_error"] = proc.stderr.strip()

        return result
    finally:
        # Always return to the original branch so the operator's
        # working tree isn't left on the auto-branch unexpectedly.
        if current_branch:
            _git(["checkout", current_branch], cwd=cwd)


__all__ = ["generate_pr"]
