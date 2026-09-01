"""Detect near-duplicate SKILL packages under runtime/execution/all_skills/.

Two packages are flagged as a duplicate pair when:

  * They live in sibling directories under ``all_skills/``.
  * Every file outside ``SKILL.md`` and ``README.md`` is byte-identical.
  * The combined non-frontmatter prose in their SKILL.md is highly
    similar (Levenshtein-style ratio ≥ 0.85) — i.e. the same skill
    rebranded under two trigger names.

Pairs that meet those tests should be consolidated into a single
canonical package using the SKILL.md ``aliases:`` field, e.g.::

    ---
    name: cashflow-valuation
    aliases: [discounted-cashflow-model]
    ---

Run::

    python tools/lint/skill_dedup_check.py            # report
    python tools/lint/skill_dedup_check.py --strict   # exit 1 on hits

Designed to plug into CI so new physical duplicates can't sneak in.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ALL_SKILLS = REPO_ROOT / "runtime" / "execution" / "all_skills"

# Files whose differences don't break the "same skill" claim.
_PROSE_FILES = {"SKILL.md", "README.md"}


def _content_signature(skill_dir: Path) -> str:
    """Hash every non-prose file in the package; differing only in
    SKILL.md / README.md leaves the signature unchanged."""
    h = hashlib.sha256()
    for path in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        if "__pycache__" in path.parts:
            continue
        if path.name in _PROSE_FILES:
            continue
        rel = path.relative_to(skill_dir).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    return text[end + 4 :].strip()


def _prose_ratio(a: Path, b: Path) -> float:
    try:
        ta = _strip_frontmatter(a.read_text(encoding="utf-8"))
        tb = _strip_frontmatter(b.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return 0.0
    if not ta and not tb:
        return 1.0
    return difflib.SequenceMatcher(None, ta, tb).ratio()


def find_duplicates(threshold: float = 0.85) -> list[tuple[Path, Path, float]]:
    if not ALL_SKILLS.is_dir():
        return []
    skills = sorted(p for p in ALL_SKILLS.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())

    by_sig: dict[str, list[Path]] = {}
    for s in skills:
        try:
            by_sig.setdefault(_content_signature(s), []).append(s)
        except OSError:
            continue

    pairs: list[tuple[Path, Path, float]] = []
    for siblings in by_sig.values():
        if len(siblings) < 2:
            continue
        for i in range(len(siblings)):
            for j in range(i + 1, len(siblings)):
                a, b = siblings[i], siblings[j]
                ratio = _prose_ratio(a / "SKILL.md", b / "SKILL.md")
                if ratio >= threshold:
                    pairs.append((a, b, ratio))
    return pairs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="prose-similarity ratio above which a sibling pair is flagged",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any duplicates are found",
    )
    args = p.parse_args()

    pairs = find_duplicates(threshold=args.threshold)
    if not pairs:
        print(f"OK · no duplicate skill packages under {ALL_SKILLS.relative_to(REPO_ROOT)}/")
        return 0

    print(f"Found {len(pairs)} likely duplicate pair(s):")
    for a, b, ratio in pairs:
        print(
            f"  {a.name} ↔ {b.name}  (prose ratio {ratio:.2f}, scripts byte-identical)",
        )
    print(
        "\nFix: pick one canonical name, add the other under the SKILL.md "
        "``aliases:`` list, then delete the duplicate directory. Loader "
        "support added in runtime/execution/suckers/market_skills.py.",
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
