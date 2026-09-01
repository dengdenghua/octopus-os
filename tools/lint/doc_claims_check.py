#!/usr/bin/env python3
"""Doc-claim drift linter: compare counts claimed in docs to filesystem reality.

Catches the common failure mode where docs say "17 个器官" but the
filesystem has 19, or "9 份契约" when there are 15.

Ground truth:
- Organs: count of markdown files under ``docs/architecture/organs/``
- Protocols: count of ``protocols/*.md`` (excluding README.md)

Also validates that every protocol file carries a YAML frontmatter block
with an ``implementation_status`` field (implemented | partial | spec_only |
dormant) and, when status is implemented/partial, a non-empty
``implemented_in`` list whose paths exist on disk.

Run::

    python tools/lint/doc_claims_check.py           # human-readable report
    python tools/lint/doc_claims_check.py --strict  # exit 1 on drift
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Valid values for the implementation_status frontmatter field.
VALID_STATUSES = frozenset({"implemented", "partial", "spec_only", "dormant"})
# Statuses that require implemented_in paths to exist on disk.
STATUSES_REQUIRING_EVIDENCE = frozenset({"implemented", "partial"})

# (regex pattern, label, ground-truth fn)
ROOT = Path(__file__).parent.parent.parent

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "19 个器官" / "20+ 个器官" / "17 器官" — bare numbers and N+ ranges
    (re.compile(r"(\d+)\+?\s*(?:个)?\s*器官"), "biomimetic"),
    (re.compile(r"(\d+)\s*份契约"), "protocols"),
    (re.compile(r"(\d+)\s*份协议"), "protocols"),
    (re.compile(r"(\d+)\s*份全齐"), "protocols"),
]


def truth_organs() -> int:
    organs = ROOT / "docs" / "architecture" / "organs"
    if not organs.exists():
        return -1
    return sum(1 for p in organs.glob("*.md") if p.name.lower() != "readme.md")


def truth_protocols() -> int:
    proto = ROOT / "protocols"
    if not proto.exists():
        return -1
    return sum(1 for p in proto.glob("*.md") if p.name.lower() != "readme.md")


TRUTH = {
    "biomimetic": truth_organs,
    "protocols": truth_protocols,
}

DOC_GLOBS = [
    "README.md",
    "docs/**/*.md",
    "protocols/README.md",
]

# Lines that name another file's count are excluded from the linter
# (cross-references should match anyway, but the noise is high). Skip
# CHANGELOG entirely — it intentionally keeps historical snapshots.
# Skip tiers.md — it describes phased scope (MVP=8, Core=13, Full=19),
# not current state drift.
SKIP_FILES = {"CHANGELOG.md", "tiers.md"}


def scan_docs() -> list[tuple[Path, int, str, int, int]]:
    """Return [(path, lineno, snippet, claimed, truth), ...] for drift."""
    drift: list[tuple[Path, int, str, int, int]] = []
    seen_files: set[Path] = set()

    for glob in DOC_GLOBS:
        for path in ROOT.glob(glob):
            if path in seen_files or path.name in SKIP_FILES:
                continue
            seen_files.add(path)
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(lines, 1):
                for pattern, truth_key in PATTERNS:
                    for match in pattern.finditer(line):
                        try:
                            claimed = int(match.group(1))
                        except (ValueError, IndexError):
                            continue
                        truth = TRUTH[truth_key]()
                        if truth < 0:
                            continue
                        # "20+" form: accept iff truth >= claimed AND truth < claimed+5
                        is_plus_form = "+" in match.group(0)
                        if is_plus_form:
                            if truth < claimed or truth >= claimed + 5:
                                drift.append((path, lineno, line.strip(), claimed, truth))
                        else:
                            if claimed != truth:
                                drift.append((path, lineno, line.strip(), claimed, truth))
    return drift


def scan_protocol_frontmatter() -> list[tuple[Path, str]]:
    """Validate ``implementation_status`` frontmatter on every protocol.

    Returns a list of ``(path, message)`` issues. Empty list = OK.
    """
    issues: list[tuple[Path, str]] = []
    proto_dir = ROOT / "protocols"
    if not proto_dir.is_dir():
        return issues

    for md in sorted(proto_dir.glob("*.md")):
        if md.name.lower() == "readme.md":
            continue
        text = md.read_text(encoding="utf-8")
        # Parse the YAML frontmatter block (--- delimited) at the very top.
        if not text.startswith("---"):
            issues.append((md, "missing YAML frontmatter (expected leading '---')"))
            continue
        end = text.find("\n---", 3)
        if end < 0:
            issues.append((md, "frontmatter block not closed (missing closing '---')"))
            continue
        block = text[4:end]

        # implementation_status (required)
        m = re.search(r"^implementation_status:\s*(\S+)", block, re.MULTILINE)
        if not m:
            issues.append((md, "missing implementation_status field"))
            continue
        status = m.group(1).strip()
        if status not in VALID_STATUSES:
            issues.append(
                (
                    md,
                    f"invalid implementation_status '{status}' "
                    f"(must be one of {sorted(VALID_STATUSES)})",
                )
            )
            continue

        # implemented_in (required non-empty for implemented/partial)
        if status in STATUSES_REQUIRING_EVIDENCE:
            paths = re.findall(r"^  - (.+)$", block, re.MULTILINE)
            if not paths:
                issues.append((md, f"implementation_status={status} but implemented_in is empty"))
                continue
            for rel in paths:
                rel = rel.strip().strip('"').strip("'")
                if rel and not (ROOT / rel).exists():
                    issues.append((md, f"implemented_in path does not exist: {rel}"))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 on drift")
    args = parser.parse_args()

    drift = scan_docs()
    fm_issues = scan_protocol_frontmatter()

    organs_truth = truth_organs()
    protos_truth = truth_protocols()
    print(
        f"Ground truth: {organs_truth} organs (docs/architecture/organs/), "
        f"{protos_truth} protocols (protocols/*.md)."
    )

    if not drift and not fm_issues:
        print("OK · no doc-claim drift detected, protocol frontmatter valid.")
        return 0

    if drift:
        print(f"\n{len(drift)} drift{'s' if len(drift) > 1 else ''} found:")
        for path, lineno, snippet, claimed, truth in drift:
            rel = path.relative_to(ROOT)
            snippet_short = snippet[:90] + "..." if len(snippet) > 90 else snippet
            print(f"  {rel}:{lineno}: claims {claimed} but truth={truth}")
            print(f"    > {snippet_short}")

    if fm_issues:
        print(f"\n{len(fm_issues)} protocol frontmatter issue(s) found:")
        for path, msg in fm_issues:
            rel = path.relative_to(ROOT)
            print(f"  {rel}: {msg}")

    if args.strict:
        print(
            "\n::error::Doc-claim drift or frontmatter issue detected. Update "
            "docs to match filesystem ground truth, or update truth fn in "
            "this linter if the structure intentionally changed."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
