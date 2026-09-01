"""Enforce the repository root hygiene contract declared in ROOT_LAYOUT.md.

ROOT_LAYOUT.md states:

    "The repository root is a product entrance. Keep it small, predictable,
     and easy to scan. ... If a root item cannot be explained here, it
     probably should not live at root."

This linter hard-codes the **allow-list** of top-level entries (files and
directories) and fails CI if anything outside that set is committed at the
root.

The check is scoped to **git-tracked** entries. Items listed in .gitignore
(local state such as ``.venv/`` or ``node_modules/``) are intentionally
ignored here — their hygiene is .gitignore's job, not this script's.

Allowed entries are derived from ROOT_LAYOUT.md. If you genuinely need to
add a new top-level file or directory:

  1. Update ROOT_LAYOUT.md first (it is the source of truth).
  2. Update ROOT_ALLOWLIST below to match.
  3. Open a PR explaining why the new entry is a "stable source or product
     boundary" (per ROOT_LAYOUT.md rule #1).

Run::

    python tools/lint/root_hygiene.py            # report
    python tools/lint/root_hygiene.py --strict   # exit 1 on any violation
    python tools/lint/root_hygiene.py --all      # scan all root entries,
                                                   # not just git-tracked
    python tools/lint/root_hygiene.py --print-allowlist
                                                   # print the allow-list
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Files and directories that may legitimately live at the repository root.
# Source of truth: ROOT_LAYOUT.md.
#
# Categories (mirroring ROOT_LAYOUT.md):
#   - Source code directories
#   - Product asset directories
#   - Project support directories
#   - Configuration files
#   - Documentation files
#   - Build / CI files
#   - IDE / editor / docker meta files
ROOT_ALLOWLIST: set[str] = {
    # ── Source code ─────────────────────────────────────────
    "runtime",
    "frontend",
    "tests",
    # ── Product assets ──────────────────────────────────────
    "agents",
    "skills",
    "protocols",
    "prompts",
    "extensions",
    "meta_skills",
    # ── Project support ─────────────────────────────────────
    "docs",
    "demos",
    "benchmarks",
    "deploy",
    "tools",
    "scripts",
    "packaging",
    # ── Configuration files ─────────────────────────────────
    "config.example.yaml",
    "config.e2e.yaml",
    "permissions.example.json",
    "skills.lock.json",
    ".env.example",
    "pyproject.toml",
    "uv.lock",
    "MANIFEST.in",
    "mkdocs.yml",
    "Makefile",
    # ── Repository tooling ──────────────────────────────────
    "package.json",
    "pnpm-lock.yaml",
    "commitlint.config.js",
    # ── Documentation ───────────────────────────────────────
    "README.md",
    "README.en.md",
    "ROOT_LAYOUT.md",
    "QUICKSTART.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "CODE_WIKI.md",
    "CLAUDE.md",
    "LICENSE",
    "NOTICE",
    "SECURITY.md",
    # ── Container / build ───────────────────────────────────
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.full.yml",
    ".dockerignore",
    # ── CI / git ────────────────────────────────────────────
    ".github",
    ".gitignore",
    ".gitattributes",
    ".git-blame-ignore-revs",
    ".editorconfig",
    ".pre-commit-config.yaml",
    # ── .NET / OS noise that the .gitignore already covers ─
    # (none — these are dotfiles, all managed in .gitignore)
}

# Pattern-level denials: even with --strict, entries matching these
# patterns at root are *never* allowed (they should be .gitignore'd and
# shipped only locally).
PATTERN_DENYLIST: tuple[str, ...] = (
    "*.tmp",
    "tmp_*.txt",
    "*_probe.py",
    "*_scratch.py",
    "*_local.py",
    "check_*.py",
    "integrate_*.py",
    "remove_*.py",
    "restore_*.py",
    "cleanup_*.py",
    "network_*.py",
    "add_*.py",
    "expand_*.py",
    "ckg_*.py",
    "quark_*.py",
    "todesk_*.py",
    "agent_probe.py",
    "tmp_out.txt",
    "response.txt",
    "joke.txt",
    "intro.txt",
    "tmp_test_code_edit.py",
)


def _list_root_entries() -> list[str]:
    return sorted(p.name for p in REPO_ROOT.iterdir())


def _git_tracked_root_entries() -> list[str] | None:
    """Return sorted names of git-tracked entries at the root.

    Returns ``None`` if git is unavailable or this is not a git checkout;
    in that case the caller should fall back to scanning the filesystem
    and let humans triage the result.
    """
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    names: set[str] = set()
    for line in result.stdout.splitlines():
        # ``git ls-files`` prints paths relative to the repo root.
        # A top-level entry has no "/" in the path.
        if line and "/" not in line:
            names.add(line)
    return sorted(names)


def _violations(actual: list[str]) -> tuple[list[str], list[str]]:
    """Return (extra, denied) lists of items that should not be at root.

    ``actual`` is already filtered to entries that are **both** git-tracked
    **and** present on disk. Files that are tracked but already deleted
    locally are ignored here — they will leave the index on the next
    ``git add -A`` / ``git commit`` and are not a hygiene violation.
    """
    extra = [name for name in actual if name not in ROOT_ALLOWLIST]

    def _matches_pattern(name: str) -> bool:
        from fnmatch import fnmatch

        return any(fnmatch(name, pat) for pat in PATTERN_DENYLIST)

    denied = [name for name in extra if _matches_pattern(name)]
    return extra, denied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any non-allowlisted root entry is found.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="scan_all",
        help="Scan every root entry on disk (not just git-tracked). "
        "Useful for local cleanup passes.",
    )
    parser.add_argument(
        "--print-allowlist",
        action="store_true",
        help="Print the allow-list and exit.",
    )
    args = parser.parse_args()

    if args.print_allowlist:
        print(f"Root allow-list ({len(ROOT_ALLOWLIST)} entries):")
        for name in sorted(ROOT_ALLOWLIST):
            print(f"  - {name}")
        return 0

    if args.scan_all:
        actual = _list_root_entries()
        scope_label = "all root entries on disk"
    else:
        tracked = _git_tracked_root_entries()
        if tracked is None:
            actual = _list_root_entries()
            scope_label = "all root entries on disk (git unavailable, fell back)"
        else:
            on_disk = set(_list_root_entries())
            # Only flag entries that are tracked AND still on disk.
            # Tracked-but-deleted entries are an in-progress commit, not a
            # hygiene violation.
            actual = sorted(set(tracked) & on_disk)
            scope_label = "git-tracked root entries that still exist on disk"
    extra, denied = _violations(actual)

    if not extra:
        print(f"OK · root is clean ({len(actual)} entries checked, all in allow-list).")
        print(f"Scope: {scope_label}.")
        return 0

    print(f"Root hygiene issues found (scope: {scope_label}):")
    if denied:
        print("\n  Pattern denylist violations (should also be in .gitignore):")
        for name in denied:
            print(f"    - {name}")
    non_denied = [n for n in extra if n not in set(denied)]
    if non_denied:
        print(
            "\n  Entries not in allow-list (consider moving under tools/, "
            "scripts/, docs/, demos/, etc., or update ROOT_LAYOUT.md "
            "and ROOT_ALLOWLIST):"
        )
        for name in non_denied:
            print(f"    - {name}")
    print("\nTotal: {} extra entr{} at root.".format(len(extra), "y" if len(extra) == 1 else "ies"))

    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
