#!/usr/bin/env python3
"""Count live pytest test items for CHANGELOG / ROADMAP references.

Usage:
    python tools/lint/count_tests.py         # prints count
    python tools/lint/count_tests.py --check # exits 1 if docs are stale

Reads pytest collection output, parses the "N items collected" line,
returns the count. Use this to replace hardcoded "3802 tests" /
"3800+ tests" references with a living number.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def count_live_tests() -> int:
    """Run pytest --collect-only and parse the item count."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        if " selected" in line:
            # "23 passed, 5968 deselected, 1 warning" → skip
            continue
        # Look for: "3802 tests collected in 2.34s"
        if "collected" in line:
            parts = line.split()
            try:
                return int(parts[0])
            except (ValueError, IndexError):
                continue
    raise RuntimeError(f"Could not parse test count from pytest --collect-only:\n{result.stdout}")


def check_docs_fresh(live_count: int) -> int:
    """Exit 1 if CHANGELOG.md or docs/roadmap.md contain stale counts.

    Rules:
    - CHANGELOG historical entries (snapshot numbers) are allowed.
    - The "current state" line in docs/roadmap.md uses "NNNN+" form;
      we accept it iff live >= NNNN AND live < NNNN+200 (drift cap).
    """
    import re

    root = Path(__file__).parent.parent.parent
    roadmap = root / "docs" / "roadmap.md"

    stale: list[str] = []

    if roadmap.exists():
        text = roadmap.read_text(encoding="utf-8")
        # Capture the first "NNNN+ tests" pattern in the snapshot line
        match = re.search(r"(\d{3,5})\+ tests", text)
        if match:
            claimed_floor = int(match.group(1))
            # Below the floor → docs over-promise
            if live_count < claimed_floor:
                stale.append(
                    f"{roadmap.relative_to(root)}: claims {claimed_floor}+ but live={live_count}"
                )
            # 200+ above → docs under-report; round up
            elif live_count >= claimed_floor + 200:
                rounded = (live_count // 100) * 100
                stale.append(
                    f"{roadmap.relative_to(root)}: claims {claimed_floor}+ "
                    f"but live={live_count} — bump to {rounded}+"
                )

    if stale:
        print("::error::Stale test counts in docs:", file=sys.stderr)
        for issue in stale:
            print(f"  {issue}", file=sys.stderr)
        print(
            f"\nLive count is now {live_count}. Update the documented floor; "
            "this command only measures and checks it.",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if CHANGELOG/ROADMAP contain stale counts",
    )
    args = parser.parse_args()

    try:
        count = count_live_tests()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.check:
        return check_docs_fresh(count)

    print(count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
