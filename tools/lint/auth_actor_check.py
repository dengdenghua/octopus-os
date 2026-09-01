"""Lint: every ``_auth(request)`` call should bind its return.

Discarding the actor returned by ``_auth`` was the root cause of the
horizontal-privilege-escalation holes audited and fixed in
``team_tasks_router.py`` and ``agents_router.py`` (LocalPartner). The
pattern is::

    def list_X(request: Request, ...):
        _auth(request)            # ← actor discarded, only checks credentials
        ...                       #   no membership / role enforcement

The right shape is::

    def list_X(request: Request, ...):
        actor = _auth(request)    # ← keep actor
        _require_member(actor, X) # ← enforce resource ownership
        ...

Baseline strategy
-----------------
The codebase has ~80 pre-existing bare-``_auth`` callsites. Auditing
each one needs domain knowledge (some endpoints are genuinely
actor-agnostic). Instead of stalling on a 1-2 day audit, we:

  1. Snapshot the current count per file in ``BASELINE`` below.
  2. Fail CI when *any* file's count goes UP.
  3. New endpoints must either bind the return or add the inline
     ``# AUTH-OK: actor-agnostic`` opt-out marker.

When auditing a file, drop its baseline count to the new value. The
pattern is a one-way ratchet: counts only go down.

Run::

    python tools/lint/auth_actor_check.py            # human-readable report
    python tools/lint/auth_actor_check.py --strict   # exit 1 on regression
    python tools/lint/auth_actor_check.py --update-baseline  # rewrite baseline
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The function names whose return value carries the actor identity.
# Discarding the call is a smell; binding it acknowledges the actor
# may matter.
AUTH_FUNCS: frozenset[str] = frozenset({"_auth", "_resolve_actor"})

# Files we scan. Routers are where endpoints live.
SCAN_GLOBS = (
    "runtime/sensing/siphon/*_router.py",
    "runtime/platform/ui/*_router.py",
)

# Inline opt-out marker on the discarded-call line. Use a custom
# prefix instead of ruff's directive syntax so we don't clash with
# ruff's parser.
NOQA_MARKER = "AUTH-OK: actor-agnostic"

# ── Baseline (ratchet) ─────────────────────────────────────────────
#
# Per-file allowed count of bare _auth(...) calls. Counts may only
# DECREASE — any increase is a regression and fails CI.
#
# Drop a count when you audit the file and either:
#   * bind the actor and enforce a check, OR
#   * add an inline ``# AUTH-OK: actor-agnostic`` after deciding
#     the endpoint is genuinely actor-agnostic.
#
# Last refresh: 2026-06-06 — see CHANGELOG.
BASELINE: dict[str, int] = {}


class _DiscardedAuthVisitor(ast.NodeVisitor):
    """Collect every Expr-statement whose value is ``_auth(...)``."""

    def __init__(self, source: str) -> None:
        self._source_lines = source.splitlines()
        self.hits: list[tuple[int, str, str]] = []

    def visit_Expr(self, node: ast.Expr) -> None:  # noqa: N802
        call = node.value
        if not isinstance(call, ast.Call):
            self.generic_visit(node)
            return
        func = call.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name not in AUTH_FUNCS:
            self.generic_visit(node)
            return
        # Allow opt-out: noqa marker on the same line.
        line_no = node.lineno
        line = self._source_lines[line_no - 1] if 0 < line_no <= len(self._source_lines) else ""
        if NOQA_MARKER in line:
            self.generic_visit(node)
            return
        self.hits.append((line_no, name, line.strip()))
        self.generic_visit(node)


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    visitor = _DiscardedAuthVisitor(source)
    visitor.visit(tree)
    return visitor.hits


def scan_repo() -> list[tuple[Path, int, str, str]]:
    hits: list[tuple[Path, int, str, str]] = []
    seen: set[Path] = set()
    for glob in SCAN_GLOBS:
        for path in REPO_ROOT.glob(glob):
            if path in seen:
                continue
            seen.add(path)
            for line_no, func, line in scan_file(path):
                hits.append((path, line_no, func, line))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 on regression")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="print updated BASELINE block (paste into this file)",
    )
    args = parser.parse_args()

    hits = scan_repo()
    counts: dict[str, int] = {}
    for path, _line_no, _func, _line in hits:
        rel = path.relative_to(REPO_ROOT).as_posix()
        counts[rel] = counts.get(rel, 0) + 1

    if args.update_baseline:
        print("# Paste this block into auth_actor_check.py BASELINE:")
        print("BASELINE: dict[str, int] = {")
        for rel in sorted(counts):
            print(f'    "{rel}": {counts[rel]},')
        print("}")
        return 0

    regressions: list[str] = []
    cleared: list[str] = []
    for rel, current in sorted(counts.items()):
        allowed = BASELINE.get(rel, 0)
        if current > allowed:
            regressions.append(
                f"  {rel}: {current} bare _auth/_resolve_actor calls (baseline allows {allowed})"
            )
    for rel, allowed in BASELINE.items():
        if rel not in counts:
            cleared.append(rel)
            continue
        if counts[rel] < allowed:
            cleared.append(f"{rel}: {counts[rel]} (baseline {allowed} — drop the baseline)")

    if not regressions:
        if cleared:
            print("OK · no regression. Files cleared / improved:")
            for line in cleared:
                print(f"  - {line}")
            print(
                "\nDrop the baseline counts in auth_actor_check.py to lock "
                "the improvement in (or run --update-baseline)."
            )
        else:
            total = sum(counts.values())
            print(f"OK · {total} bare _auth(...) calls match baseline. No regression.")
        return 0

    print(f"{len(regressions)} file(s) regressed:")
    for line in regressions:
        print(line)

    if args.strict:
        print(
            "\n::error::New bare _auth(...) call detected. Either bind "
            "the return\n"
            "(``actor = _auth(request)`` and use it) or add an explicit\n"
            f"``# {NOQA_MARKER}`` comment if the endpoint is genuinely\n"
            "actor-agnostic. See team_tasks_router.py / agents_router.py\n"
            "LocalPartner for the membership-check pattern."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
