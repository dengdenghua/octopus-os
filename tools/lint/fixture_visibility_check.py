"""Guard: no test fixture input may be hidden from git.

Twice in one week a blanket ``.gitignore`` suffix rule swallowed a file the
tests *read*:

* ``benchmarks/fixtures/security.denied-destructive-action/data.db`` and
  ``.../security.untrusted-instructions/external-contact.log`` — caught by
  ``*.db`` / ``*.log``.
* ``frontend/src/core/realtime/__fixtures__/replay-golden.events.jsonl`` —
  caught by ``*.jsonl``.

The failure mode is nasty because it is invisible locally: the developer's
checkout still holds the file (created by an earlier run, or written by hand),
so the suite is green. Every fresh clone — and CI — gets a bare ``ENOENT`` from
a test whose logic is fine.

This check walks the known fixture roots and fails on any file that exists on
disk but is ignored by git. Junk that legitimately belongs to nobody
(``.DS_Store``, ``__pycache__``) is exempt.

Run standalone or via ``make lint``:

    .venv/bin/python -m tools.lint.fixture_visibility_check
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories whose contents are, by definition, committed test inputs.
FIXTURE_ROOTS: tuple[str, ...] = (
    "benchmarks/fixtures",
    "tools/lint/fixtures",
    "frontend/src/core/realtime/__fixtures__",
)

# Names that are never fixture inputs even when they land in a fixture dir.
EXEMPT_NAMES: frozenset[str] = frozenset({".DS_Store", "Thumbs.db", ".gitkeep"})
EXEMPT_DIR_PARTS: frozenset[str] = frozenset({"__pycache__", ".pytest_cache", "node_modules"})


def _iter_fixture_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in EXEMPT_NAMES:
            continue
        if EXEMPT_DIR_PARTS & set(path.parts):
            continue
        out.append(path)
    return out


def _ignored(paths: list[Path], repo_root: Path) -> list[Path]:
    """Return the subset git would ignore.

    ``check-ignore`` exits 1 when nothing matches, which is not an error here,
    so the return code is deliberately not checked.
    """
    if not paths:
        return []
    rel = [str(p.relative_to(repo_root)) for p in paths]
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(repo_root), "check-ignore", "--", *rel],
        capture_output=True,
        text=True,
        check=False,
    )
    hits = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return [repo_root / h for h in sorted(hits)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repository root to scan (defaults to this checkout).",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    candidates: list[Path] = []
    for rel_root in FIXTURE_ROOTS:
        candidates.extend(_iter_fixture_files(repo_root / rel_root))

    hidden = _ignored(candidates, repo_root)
    if hidden:
        print(
            "fixture-visibility: these test inputs exist on disk but are "
            "ignored by git, so they are missing on every fresh clone:",
            file=sys.stderr,
        )
        for path in hidden:
            rel = path.relative_to(repo_root)
            why = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["git", "-C", str(repo_root), "check-ignore", "-v", "--", str(rel)],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            print(f"  {rel}\n      matched by {why or 'an unknown rule'}", file=sys.stderr)
        print(
            "\nFix: add a negation to .gitignore (e.g. "
            "'!benchmarks/fixtures/**/*.db') and commit the file.",
            file=sys.stderr,
        )
        return 1

    print(f"fixture-visibility: {len(candidates)} fixture file(s) tracked, 0 hidden")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
