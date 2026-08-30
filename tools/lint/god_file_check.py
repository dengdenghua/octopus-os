"""Forbid new god files (≥ a configured line count) under runtime/.

Current state has 15 files ≥1000 lines. Splitting them is risky and
takes time, so we don't block the project on that — but we DO want to
prevent NEW god files from sneaking in. This linter:

  * Captures the existing list in ``god_files_baseline.txt``.
  * On each run, reports any file ≥ THRESHOLD that's not on the
    baseline (= a new god file → fails).
  * Reports baseline entries that have shrunk below THRESHOLD or
    been removed (= contributors actually split a file → must be
    removed from baseline to lock in the win).

Run::

    python tools/lint/god_file_check.py            # report
    python tools/lint/god_file_check.py --strict   # exit 1 on new gods or stale baseline
    python tools/lint/god_file_check.py --write-baseline
                                                   # snapshot current state
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_PATH = REPO_ROOT / "tools" / "lint" / "god_files_baseline.txt"

# Files at or above this line count are flagged. The threshold is
# intentionally generous — most healthy modules sit at 200-600 lines,
# but several legitimate ones (cerebrum/react_loop after refactor,
# core protocol files) push 1000-1500 without being unmanageable.
THRESHOLD_LINES = 1000

# ESCROW threshold: files already ≥ this size cannot grow any further.
# Any line increase is a regression. Targets realtime_cerebrum.py (~4k
# lines) and other 3k+ monsters to prevent unbounded sprawl. Files in
# this range MUST either shrink (split into modules) or stay flat —
# they can't grow until they're back under the threshold.
ESCROW_THRESHOLD = 4000

# Skip generated / vendored / scaffolded code.
_EXCLUDE_PARTS: tuple[str, ...] = (
    "all_skills",  # third-party-style scripts; out of CI scope
    "__pycache__",
    "tools/lint/fixtures",
)


def _scan() -> list[tuple[Path, int]]:
    hits: list[tuple[Path, int]] = []
    for p in (REPO_ROOT / "runtime").rglob("*.py"):
        rel_str = str(p).replace("\\", "/")
        if any(part in rel_str for part in _EXCLUDE_PARTS):
            continue
        try:
            count = sum(1 for _ in p.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            continue
        if count >= THRESHOLD_LINES:
            hits.append((p, count))
    hits.sort(key=lambda h: -h[1])
    return hits


def _load_baseline() -> dict[str, int]:
    """Return baseline as {path: line_count}. Backward-compat: older
    baseline lines without a count are read as 0 (escrow check
    skipped for them)."""
    if not _BASELINE_PATH.is_file():
        return {}
    out: dict[str, int] = {}
    for raw in _BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Each line is "<path>\t<lines>"
        parts = line.split("\t", 1)
        path = parts[0]
        count = 0
        if len(parts) == 2:
            try:
                count = int(parts[1])
            except ValueError:
                count = 0
        out[path] = count
    return out


def _write_baseline(hits: list[tuple[Path, int]]) -> None:
    lines = [
        f"# God files ≥{THRESHOLD_LINES} lines, captured by tools/lint/god_file_check.py",
        "# Splitting one of these? Remove its line from this file so the",
        "# audit can lock in your win.",
        "",
    ]
    for p, n in sorted(hits, key=lambda h: str(h[0])):
        rel = p.relative_to(REPO_ROOT).as_posix()
        lines.append(f"{rel}\t{n}")
    _BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _audit(strict: bool) -> int:
    hits = _scan()
    baseline = _load_baseline()
    seen_paths = {p.relative_to(REPO_ROOT).as_posix() for p, _ in hits}
    baseline_paths = set(baseline.keys())

    new_gods = [
        (p, n) for p, n in hits if p.relative_to(REPO_ROOT).as_posix() not in baseline_paths
    ]
    shrunk = baseline_paths - seen_paths

    # Escrow check: files at or above ESCROW_THRESHOLD MUST NOT grow.
    escrow_breaches: list[tuple[str, int, int]] = []
    for p, current in hits:
        rel = p.relative_to(REPO_ROOT).as_posix()
        if rel not in baseline:
            continue  # new gods are caught by the new_gods branch
        baseline_count = baseline[rel]
        if baseline_count >= ESCROW_THRESHOLD and current > baseline_count:
            escrow_breaches.append((rel, baseline_count, current))

    if not new_gods and not shrunk and not escrow_breaches:
        print(
            f"OK · {len(baseline)} baseline god files unchanged "
            f"(threshold {THRESHOLD_LINES} lines, escrow {ESCROW_THRESHOLD})"
        )
        return 0

    if shrunk:
        print(f"{len(shrunk)} baseline file(s) shrunk below threshold:")
        for entry in sorted(shrunk):
            print(f"  SHRUNK  {entry}")
        print(
            "\nRemove them from "
            f"{_BASELINE_PATH.relative_to(REPO_ROOT).as_posix()} so the "
            "split-up gain is locked in."
        )

    if escrow_breaches:
        print(
            f"\n{len(escrow_breaches)} ESCROW BREACH "
            f"(file ≥{ESCROW_THRESHOLD} lines must not grow):"
        )
        for rel, baseline_count, current in escrow_breaches:
            delta = current - baseline_count
            print(f"  GREW  +{delta:5d}  {rel} ({baseline_count} → {current})")
        print(
            "\nFiles in escrow are too large to allow further growth. "
            "Either:\n"
            "  1. Refactor to bring the line count DOWN, or\n"
            "  2. Split the file into modules so it falls below the\n"
            f"     {ESCROW_THRESHOLD}-line escrow threshold."
        )

    if new_gods:
        print(f"\n{len(new_gods)} NEW god file(s) ≥{THRESHOLD_LINES} lines:")
        for p, n in new_gods:
            rel = p.relative_to(REPO_ROOT).as_posix()
            print(f"  NEW    {n:5d}  {rel}")
        print(
            "\nFix one of:\n"
            "  1. Split the file (preferred — see runtime/core/cerebrum/ "
            "for an example of breaking one big module into 7 focused subs)\n"
            "  2. If genuinely irreducible, raise THRESHOLD_LINES in "
            f"{__file__!s}\n"
            "     and document the reason."
        )

    if strict and (new_gods or shrunk or escrow_breaches):
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--strict", action="store_true", help="exit 1 on new god files or stale baseline entries"
    )
    p.add_argument(
        "--write-baseline", action="store_true", help="snapshot current state to baseline file"
    )
    args = p.parse_args()

    if args.write_baseline:
        hits = _scan()
        _write_baseline(hits)
        rel = _BASELINE_PATH.relative_to(REPO_ROOT).as_posix()
        print(f"wrote {len(hits)} entries to {rel}")
        return 0

    return _audit(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
