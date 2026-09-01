"""Lint: ratchet down cross-directory skill name duplication.

Skills currently live in two physical locations:
  * ``runtime/execution/all_skills/<name>/SKILL.md`` — historical
  * ``skills/public/<name>/SKILL.md`` — preferred (loader auto-discovers)

When a skill name exists in BOTH locations the loader's behavior is
ambiguous: ``_add_file_backed_skill_catalog()`` enumerates whatever
``Path(__file__).parent.iterdir()`` finds first, then ``register_market``
re-scans ``skills/public/``. The second scan wins on metadata but the
first one's presence-side-effects (catalog entries) leak through.

The wheel-shrinking change in 2026-06 (MANIFEST.in stops shipping
file-backed SKILL.md packs) made the issue worse: the wheel contains
no SKILL.md, dev installs see all 175, and the 47 names that exist in
BOTH places confuse skill-id resolution.

This linter:
  1. Reports names appearing in both ``runtime/execution/all_skills/``
     and ``skills/public/``.
  2. Records the current count as a baseline. Any growth fails CI.
  3. Encourages migration: when a duplicate is removed (preferably
     by deleting the ``runtime/execution/all_skills/`` copy and
     keeping the ``skills/public/`` one), the baseline must be
     dropped to lock in the win.

Run::

    python tools/lint/skill_cross_dir_check.py            # report
    python tools/lint/skill_cross_dir_check.py --strict   # exit 1 on growth
    python tools/lint/skill_cross_dir_check.py --update-baseline
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_DIR = REPO_ROOT / "runtime" / "execution" / "all_skills"
PUBLIC_DIR = REPO_ROOT / "skills" / "public"

# Files at the skill-pack root that mark a SKILL.md-backed dir.
_SKILL_MD = "SKILL.md"

# Baseline count of cross-dir duplicates. Update when you migrate or
# resolve duplicates. Audited 2026-06-25 (skills/local/ retired).
BASELINE_DUPLICATE_COUNT = 4


def _skill_names_in(root: Path) -> set[str]:
    """Names of subdirs containing SKILL.md."""
    if not root.is_dir():
        return set()
    return {p.name for p in root.iterdir() if p.is_dir() and (p / _SKILL_MD).is_file()}


def find_duplicates() -> set[str]:
    legacy = _skill_names_in(LEGACY_DIR)
    public = _skill_names_in(PUBLIC_DIR)
    return legacy & public


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 on growth")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="print updated BASELINE_DUPLICATE_COUNT to paste in",
    )
    args = parser.parse_args()

    duplicates = find_duplicates()
    count = len(duplicates)

    if args.update_baseline:
        print("# Paste into skill_cross_dir_check.py:")
        print(f"BASELINE_DUPLICATE_COUNT = {count}")
        return 0

    if count <= BASELINE_DUPLICATE_COUNT:
        if count < BASELINE_DUPLICATE_COUNT:
            print(
                f"OK · {count} cross-dir skill duplicates "
                f"(baseline {BASELINE_DUPLICATE_COUNT} — drop the baseline "
                "to lock in your migration)."
            )
        else:
            print(f"OK · {count} cross-dir skill duplicates match baseline.")
        return 0

    new_dupes = count - BASELINE_DUPLICATE_COUNT
    print(
        f"REGRESSION: {count} cross-dir skill duplicates "
        f"(baseline {BASELINE_DUPLICATE_COUNT}, +{new_dupes} new):"
    )
    for name in sorted(duplicates):
        print(f"  - {name}")
    print(
        "\nNew skill-name overlap between runtime/execution/all_skills/ and "
        "skills/public/.\n"
        "When adding a new skill, prefer skills/public/ and remove any "
        "stale all_skills/ copy.\n"
        "If the duplication is intentional (e.g., during a migration), "
        "update BASELINE_DUPLICATE_COUNT in this file."
    )

    if args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
