"""Guard: no source file may sit untracked in a source root.

2026-08-28 audit (AUDIT_2026-08-28.md, P0-1): 26 ``runtime/**/*.py`` modules
and 44 test files had never been ``git add``-ed.  Nine of the runtime modules
are imported by *tracked* code (``runtime/execution/loops/__init__.py:1`` and
friends), so every clean checkout — including CI, which uses
``actions/checkout@v4`` — failed with ``ModuleNotFoundError`` while the
developer's machine ran green.  The failure mode is the same nasty one
``fixture_visibility_check`` guards against: invisible locally, fatal for
everyone else.

Root cause was a plain forgotten ``git add`` (``git check-ignore -v`` returns
nothing for these paths), so the fix is prevention: fail loudly whenever a
source file exists on disk inside a source root but is not tracked and not
ignored.

Run standalone or via ``make lint``:

    .venv/bin/python -m tools.lint.untracked_source_check
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Source roots whose files must never linger untracked.  Deliberately narrow:
# scratch notes and audit reports at the repo root are a workflow choice, not
# a release risk.
SOURCE_ROOTS: tuple[str, ...] = (
    "runtime/",
    "tests/",
    "tools/",
    "scripts/",
    "frontend/src/",
    "deploy/",
    "packaging/",
)


def _untracked_files(root: Path) -> list[str]:
    """Return untracked (and not ignored) files, NUL-safe for odd filenames."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=False,
        check=True,
    )
    return [p.decode("utf-8", "surrogateescape") for p in out.stdout.split(b"\0") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    untracked = _untracked_files(REPO_ROOT)
    offenders = sorted(
        path for path in untracked if any(path.startswith(prefix) for prefix in SOURCE_ROOTS)
    )
    if not offenders:
        print("OK · no untracked source files under source roots")
        return 0

    print(
        f"FAIL · {len(offenders)} untracked source file(s) — they are invisible "
        "to CI and to every fresh clone:\n",
        file=sys.stderr,
    )
    for path in offenders:
        print(f"  UNTRACKED  {path}", file=sys.stderr)
    print(
        "\nFix: git add the files above (after checking they are not build "
        "artifacts).  If a path should stay local, move it out of the source "
        "roots or add an explicit .gitignore rule.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
