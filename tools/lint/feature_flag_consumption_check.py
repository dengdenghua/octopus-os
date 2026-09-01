"""Feature-flag consumption audit · flag flags that nobody reads.

A feature flag registered in ``runtime/platform/runtime_policy/feature_flags.py``
but never consumed via ``is_on("<name>")`` / ``value("<name>")`` anywhere in
``runtime/`` is "declared, not routed": it reads as a working toggle to an
operator but flipping it does nothing. This is the most common wiring debt in
this codebase (a whole audit batch was this class). This linter catches the
NEXT dead flag the moment it lands.

How it works
------------
  * Registered set: every ``name="..."`` keyword in the flag-registration file
    (AST-parsed, so it survives reformatting).
  * Consumed set: every ``is_on("X")`` / ``value("X")`` (single or double
    quotes) found in any ``runtime/*.py``.
  * A registered flag whose name is in NO consumer is an offender.
  * ``feature_flag_consumption_baseline.txt`` grandfathers today's offenders.
    A run reports offenders NOT on the baseline (a newly-dead flag) and STALE
    baseline entries (a flag that got wired or removed — drop the line to lock
    the win).

Limits (honest)
---------------
Static: a flag read via a computed name (``is_on(f"x.{k}")``) or via
``os.environ`` directly shows as unconsumed and must be baselined — which then
DOCUMENTS that the flag isn't going through the flag system. The point is the
ratchet on NEW dead flags, not a perfect oracle.

Run::

    python tools/lint/feature_flag_consumption_check.py            # report
    python tools/lint/feature_flag_consumption_check.py --strict   # exit 1 on new
    python tools/lint/feature_flag_consumption_check.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_PATH = REPO_ROOT / "tools" / "lint" / "feature_flag_consumption_baseline.txt"
_FLAGS_FILE = REPO_ROOT / "runtime" / "platform" / "runtime_policy" / "feature_flags.py"
_RUNTIME = REPO_ROOT / "runtime"

# is_on("x") / value('x') / .is_on("x") / .value("x") — single OR double quotes.
_CONSUME_RE = re.compile(r"\b(?:is_on|value)\(\s*[\"']([a-z][a-z0-9_.]*)[\"']")


def _registered_flags() -> set[str]:
    """Flag names from every ``name="..."`` keyword in the registration file."""
    names: set[str] = set()
    try:
        tree = ast.parse(_FLAGS_FILE.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return names
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "name"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
                and "." in kw.value.value  # flag names are dotted (group.flag)
            ):
                names.add(kw.value.value)
    return names


def _consumed_flags() -> set[str]:
    """Flag names referenced by is_on()/value() anywhere under runtime/."""
    consumed: set[str] = set()
    for path in _RUNTIME.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        consumed.update(_CONSUME_RE.findall(text))
    return consumed


def _scan() -> set[str]:
    return _registered_flags() - _consumed_flags()


def _load_baseline() -> set[str]:
    if not _BASELINE_PATH.is_file():
        return set()
    out: set[str] = set()
    for raw in _BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            out.add(s)
    return out


def _write_baseline(offenders: set[str]) -> None:
    lines = [
        "# Feature flags registered but never read via is_on()/value() under",
        "# runtime/, captured by tools/lint/feature_flag_consumption_check.py.",
        "# Wired one up (or removed it)? Delete its line to lock the win.",
        "# A line for a flag read via a computed name / os.environ documents",
        "# that the flag bypasses the flag system — keep it, fix when able.",
        "",
    ]
    lines.extend(sorted(offenders))
    _BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _audit(strict: bool) -> int:
    offenders = _scan()
    baseline = _load_baseline()
    new = sorted(offenders - baseline)
    stale = sorted(baseline - offenders)

    if not new and not stale:
        print(f"OK · {len(baseline)} baseline unconsumed flag(s) unchanged")
        return 0

    if stale:
        print(f"{len(stale)} baseline flag(s) now consumed or removed:")
        for entry in stale:
            print(f"  STALE  {entry}")
        rel = _BASELINE_PATH.relative_to(REPO_ROOT).as_posix()
        print(f"\nRemove them from {rel} to lock in the win.")

    if new:
        print(f"\n{len(new)} NEW unconsumed feature flag(s) — registered, never read:")
        for entry in new:
            print(f"  NEW  {entry}")
        print(
            "\nA flag nobody reads via is_on()/value() does nothing when flipped. "
            "Fix one of:\n"
            "  1. Read it where it should gate behaviour (is_on/value), or\n"
            "  2. Remove the registration, or\n"
            "  3. If it's read via a computed name / os.environ, add it to\n"
            f"     {_BASELINE_PATH.relative_to(REPO_ROOT).as_posix()} via "
            "--write-baseline so the bypass is documented."
        )

    return 1 if (strict and (new or stale)) else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--strict", action="store_true", help="exit 1 on new unconsumed flags or stale baseline"
    )
    p.add_argument(
        "--write-baseline",
        action="store_true",
        help="snapshot current unconsumed flags to the baseline file",
    )
    args = p.parse_args()

    if args.write_baseline:
        offenders = _scan()
        _write_baseline(offenders)
        rel = _BASELINE_PATH.relative_to(REPO_ROOT).as_posix()
        print(f"wrote {len(offenders)} entries to {rel}")
        return 0

    return _audit(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
