"""Lint: no hardcoded user-specific absolute paths in committed code.

Catches the failure mode where a developer commits a path like
``C:\\Users\\alice\\Downloads\\thing.zip`` or ``/home/alice/repo`` —
machine-local strings that break on every other contributor's box.

We already have an *in-flight* ReAct guard (``react_guards.py §45``)
that catches this when an agent tries to introduce one. This linter
catches it at rest in the repo, e.g. config files, JSON state, or
human-edited code that bypassed the agent.

Allowed forms (NOT flagged):
  * ``C:\\Windows`` / ``C:\\Program Files`` — system roots
  * ``C:\\Users`` (without a username segment) — generic root
  * ``/home`` (without a username segment) — generic root
  * ``/Users/Public``, ``/Users/Shared`` — non-user-specific
  * ``/home/runner`` — GitHub Actions runner (CI)
  * Comments / docstrings — those are documentation
  * Deliberate test fixtures carrying ``# lint: allow-user-path`` on
    the same line; the marker keeps the exception narrow and reviewable

Flagged:
  * ``C:\\Users\\alice`` (a specific username)
  * ``/Users/alice`` / ``/home/alice``

Run::

    python tools/lint/hardcoded_path_check.py            # report
    python tools/lint/hardcoded_path_check.py --strict   # exit 1 on hits
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Match user-specific absolute paths. ``[A-Za-z0-9_-]+`` for username
# segment — any non-trivial sequence after the user-root prefix.
# Excluded usernames are CI / system / shared accounts that legitimately
# appear in committed code.
_EXCLUDED_USERNAMES = frozenset(
    {
        "runner",  # GitHub Actions
        "Public",  # macOS shared
        "Shared",  # macOS shared
        "Default",  # Windows default profile
        "Administrator",  # Windows admin (system-level)
        "All Users",  # Windows system
    }
)

_PATH_PATTERN = re.compile(
    r"""
    (?:
        C:[\\/]Users[\\/]
      | /Users/
      | /home/
    )
    (?P<username>[A-Za-z0-9_-]+)
    """,
    re.VERBOSE,
)

_INLINE_FIXTURE_MARKER = "# lint: allow-user-path"

# Files we scan. Skip generated / vendored content.
SCAN_GLOBS = (
    "runtime/**/*.py",
    "runtime/**/*.json",
    "runtime/**/*.yaml",
    "runtime/**/*.yml",
    "tests/**/*.py",
    "tools/**/*.py",
    "scripts/**/*.py",
    "scripts/**/*.sh",
    "config.example.yaml",
    "config.local.yaml",
    "pyproject.toml",
    ".github/**/*.yml",
)

# Files / globs to skip even if they match. These are docs about paths
# (not actual paths) or test fixtures that intentionally contain
# offending strings.
SKIP_GLOBS = (
    "runtime/core/cerebrum/react_guards.py",  # the runtime guard's own docstrings
    "runtime/core/cerebrum/react_parsing.py",  # parser comments
    "tests/test_react_guards_quality_floor.py",  # fixture data
    "tests/test_tool_intent_heuristic.py",  # fixture data
    "tests/test_build_turn_session.py",  # fixture: /home/data placeholder
    "tests/test_stable_prompt_invariant.py",  # fixture: /home/x placeholder
    "tests/test_prompt_injection.py",  # fixture: /Users/dev placeholder
    "tests/test_reflex_forge.py",  # fixture: /Users/test placeholder
    "tools/lint/hardcoded_path_check.py",  # this file
    # Standard skill documentation that mentions Windows temp paths.
    "runtime/execution/all_skills/edge-tts/SKILL.md",
    "runtime/execution/all_skills/speech-synthesis/SKILL.md",
)


def _is_skipped(rel: str) -> bool:
    rel_posix = rel.replace("\\", "/")
    return any(rel_posix == skip or rel_posix.startswith(skip) for skip in SKIP_GLOBS)


def _line_is_comment(line: str, ext: str) -> bool:
    """Cheap heuristic: skip comment lines (Python ``#``, JS/JSON ``//``,
    YAML ``#``). Doesn't handle multi-line docstrings, but those are
    handled by the SKIP_GLOBS list for the few known offenders.
    """
    stripped = line.lstrip()
    if not stripped:
        return False
    if ext in (".py", ".yaml", ".yml", ".sh", ".toml"):
        return stripped.startswith("#")
    if ext in (".js", ".ts", ".tsx", ".cjs", ".mjs"):
        return stripped.startswith("//") or stripped.startswith("*")
    return False


def scan() -> list[tuple[Path, int, str, str]]:
    """Return [(path, lineno, username, snippet), ...] for hits."""
    hits: list[tuple[Path, int, str, str]] = []
    seen: set[Path] = set()
    for glob in SCAN_GLOBS:
        for path in REPO_ROOT.glob(glob):
            if path in seen:
                continue
            seen.add(path)
            rel = path.relative_to(REPO_ROOT).as_posix()
            if _is_skipped(rel):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            ext = path.suffix.lower()
            for lineno, line in enumerate(lines, 1):
                if _line_is_comment(line, ext):
                    continue
                if _INLINE_FIXTURE_MARKER in line:
                    continue
                for match in _PATH_PATTERN.finditer(line):
                    username = match.group("username")
                    if username in _EXCLUDED_USERNAMES:
                        continue
                    # URL context: ``https://example.com/home/foo`` is
                    # a path segment in a URL, not a user dir. Skip
                    # when the match is preceded by ``://`` somewhere
                    # earlier on the line.
                    prefix = line[: match.start()]
                    if "://" in prefix:
                        continue
                    snippet = line.strip()[:120]
                    hits.append((path, lineno, username, snippet))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 on hits")
    args = parser.parse_args()

    hits = scan()
    if not hits:
        print("OK · no hardcoded user-specific paths in committed code.")
        return 0

    print(f"{len(hits)} hardcoded user-specific path(s) found:")
    for path, lineno, username, snippet in hits:
        rel = path.relative_to(REPO_ROOT).as_posix()
        print(f"  {rel}:{lineno}: username={username!r}")
        print(f"    > {snippet}")

    if args.strict:
        print(
            "\n::error::User-specific path found. Replace with:\n"
            "  * ``Path.home()`` / ``os.path.expanduser('~')`` for the user dir\n"
            "  * a config field for project-relative paths\n"
            "  * an env var for deployment-specific paths\n"
            "If you must reference a real user dir, do it via runtime\n"
            "configuration, not committed source.\n"
            "\nIf the hit is a documentation example or test fixture, add\n"
            "`# lint: allow-user-path` on that fixture line, with a nearby\n"
            "comment explaining why the path shape is part of the test."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
