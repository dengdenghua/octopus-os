"""Enforce a single canonical GitHub repository URL across the project.

The canonical URL is declared in ``pyproject.toml``::

    [tool.echo]
    repo_url = "https://github.com/<owner>/<repo>"

Every other file that mentions the project repo URL on GitHub MUST match
this value. This linter scans the working tree for ``github.com/<...>``
strings and reports any that disagree with the source of truth.

Files that are *explicitly allow-listed* (e.g. external project links in
``docs/forklist.md`` and ``docs/channels/*.md``) are skipped. The
allow-list is a set of file globs, not URL patterns, so it stays
maintainable as the project grows.

Run::

    python tools/lint/repo_url_check.py            # report
    python tools/lint/repo_url_check.py --strict   # exit 1 on any mismatch
    python tools/lint/repo_url_check.py --print-allowlist
                                                     # show the source-of-truth
                                                     # and skip globs
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Match the *project's own* repo URLs. We intentionally look for the
# current Echo repository and pre-Echo placeholders that must not reappear.
# External links to other projects (e.g. "github.com/next.js/next.js" in
# attribution pages) are NOT flagged.
GITHUB_URL_RE = re.compile(
    r"https?://(?:www\.)?github\.com/"
    r"(?P<owner>dengdenghua|echo(?:-ai|-agent)?|echo(?:-ai|-agent)?)"
    r"/(?P<repo>echo(?:-os|-agent)?|echo(?:-os|-agent)?)(?=/|$|[\s\"'#)])",
    re.IGNORECASE,
)

# Files / globs that are *not* checked. Use this for:
#   - vendored third-party docs (docs/channels/* — links to other projects)
#   - generated files
#   - lock files / build outputs
SKIP_GLOBS: tuple[str, ...] = (
    # Vendored / external-link docs — these legitimately link to OTHER
    # GitHub projects, not our own.
    "docs/channels/*",
    "docs/forklist.md",
    "docs/archive/*",
    # Build / generated
    "frontend/dist/**",
    "frontend/release/**",
    "frontend/coverage/**",
    "frontend/src/core/api/openapi-types.ts",
    "frontend/src/styles/tailwind-prebuilt.css",
    "docs/openapi-snapshot.json",
    # Tooling output
    "tools/lint/god_files_baseline.txt",
    "tools/lint/exception_audit_baseline.txt",
    "tools/lint/fixtures/**",
    # Local state
    "data/**",
    "logs/**",
    ".echo/**",
    "test-results/**",
    "frontend/test-results/**",
    # Dependency manifests that we *do* check, but transitively pull
    # in third-party links via their lockfiles. Lockfiles are excluded.
    "frontend/pnpm-lock.yaml",
    "uv.lock",
    # The linter itself can mention the canonical URL.
    "tools/lint/repo_url_check.py",
)

# The path that holds the source of truth. Excluded from the scan
# (otherwise it would self-match).
SOURCE_OF_TRUTH_PATH = "pyproject.toml"


def _load_canonical() -> str:
    """Read the canonical repo URL from ``pyproject.toml``."""
    if not PYPROJECT.is_file():
        raise SystemExit(f"pyproject.toml not found at {PYPROJECT}")
    with PYPROJECT.open("rb") as fp:
        data = tomllib.load(fp)
    try:
        return str(data["tool"]["echo"]["repo_url"])
    except KeyError as exc:
        raise SystemExit(
            "pyproject.toml is missing [tool.echo] repo_url. Add it before running this linter."
        ) from exc


def _should_skip(rel_path: str) -> bool:
    return any(fnmatch(rel_path, pat) for pat in SKIP_GLOBS)


def _is_source_of_truth(rel_path: str) -> bool:
    return rel_path == SOURCE_OF_TRUTH_PATH


def _iter_text_files() -> list[Path]:
    """Yield tracked, non-binary, non-generated text files under the repo root."""
    git_dir = REPO_ROOT / ".git"
    if git_dir.is_dir():
        # Use git ls-files to respect .gitignore.
        import subprocess

        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        names = [n for n in result.stdout.split(b"\x00") if n]
        return [REPO_ROOT / n.decode("utf-8") for n in names]
    # Fallback: walk the tree, skipping .git by hand.
    files: list[Path] = []
    for p in REPO_ROOT.rglob("*"):
        if p.is_dir():
            continue
        if ".git" in p.relative_to(REPO_ROOT).parts:
            continue
        files.append(p)
    return files


def _scan() -> tuple[str, list[tuple[Path, str, str]]]:
    canonical = _load_canonical()
    canonical_norm = canonical.rstrip("/")
    offenders: list[tuple[Path, str, str]] = []

    for path in _iter_text_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if _is_source_of_truth(rel) or _should_skip(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        for match in GITHUB_URL_RE.finditer(text):
            found = match.group(0).rstrip("/")
            if found != canonical_norm:
                offenders.append((path, match.group(0), canonical_norm))
    return canonical, offenders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any URL disagrees with the source of truth.",
    )
    parser.add_argument(
        "--print-allowlist",
        action="store_true",
        help="Print the source-of-truth URL and skip globs, then exit.",
    )
    args = parser.parse_args()

    canonical = _load_canonical()

    if args.print_allowlist:
        print("Source of truth (pyproject.toml [tool.echo].repo_url):")
        print(f"  {canonical}")
        print(f"\nSkip globs ({len(SKIP_GLOBS)}):")
        for g in SKIP_GLOBS:
            print(f"  - {g}")
        return 0

    canonical, offenders = _scan()
    if not offenders:
        print(f"OK · all repo URLs match the source of truth ({canonical}).")
        return 0

    print(
        f"Repo URL drift found · source of truth is {canonical}, "
        f"but {len(offenders)} occurrence(s) disagree:"
    )
    for path, found, want in offenders:
        rel = path.relative_to(REPO_ROOT).as_posix()
        print(f"  - {rel}: '{found}' (expected '{want}')")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
