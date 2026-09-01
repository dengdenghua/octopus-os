"""Forbid NEW ``await`` inside a synchronous-lock ``with`` block.

Holding a ``threading.Lock`` / ``RLock`` across an ``await`` suspends the
coroutine *while still holding the lock*. Every other coroutine — and, in
the WS handlers, every other socket's task — then blocks on that lock until
the suspended one resumes. Under the TestClient two-socket portal this
deadlocks intermittently; in production it serializes the event loop behind
whatever the await is waiting on. It is a real, recurring foot-gun in this
codebase's hand-rolled ``threading.Lock`` + async mix (the team-rooms WS
handler hit exactly this: a second ``with lock:`` added to the hot message
path deadlocked the 2-socket test ~1 run in 5).

The safe shape the code already follows everywhere: mutate shared state
*under* the lock, capture what you need into locals, release, then ``await``
(broadcast / send / sleep) outside the lock. ``asyncio.Lock`` — which is
*designed* to be held across ``await`` — uses ``async with`` and is therefore
never flagged: this audit only looks at plain ``with``.

  * ``async_lock_baseline.txt`` captures today's violations (currently none —
    the whole tree obeys the rule, so this is pure prevention).
  * Each run reports any ``await``-under-sync-lock NOT on the baseline
    (= a new foot-gun → fails under ``--strict``).
  * Baseline entries that no longer exist are reported as STALE so the win
    gets locked in by deleting the line.

Awaits inside a *nested* ``def`` / ``async def`` / ``lambda`` defined within
the block are NOT counted — they are not executed while the lock is held.

Run::

    python tools/lint/async_lock_check.py            # report
    python tools/lint/async_lock_check.py --strict   # exit 1 on new/stale
    python tools/lint/async_lock_check.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_PATH = REPO_ROOT / "tools" / "lint" / "async_lock_baseline.txt"

# Directories scanned. Tests are included on purpose — a deadlock in a
# fixture is just as real, and most async-lock code lives under runtime/.
_SCAN_ROOTS: tuple[str, ...] = ("runtime", "tests")

# Skip generated / vendored / scaffolded / fixture code.
_EXCLUDE_PARTS: tuple[str, ...] = (
    "all_skills",
    "__pycache__",
    "tools/lint/fixtures",
)

# A ``with`` whose context expression mentions one of these is treated as a
# lock acquisition. ``threading.Lock``/``RLock`` instances are conventionally
# named ``lock`` / ``_lock`` / ``*_lock`` / ``mutex`` in this tree; matching
# the name (rather than resolving the type) keeps the check a pure-AST,
# import-free pass. ``asyncio.Lock`` is excluded structurally — it is used
# with ``async with``, which this audit never inspects.
_LOCK_HINTS: tuple[str, ...] = ("lock", "mutex")


def _is_lock_with(node: ast.With) -> bool:
    for item in node.items:
        dumped = ast.dump(item.context_expr).lower()
        if any(hint in dumped for hint in _LOCK_HINTS):
            return True
    return False


class _AwaitFinder(ast.NodeVisitor):
    """True iff an ``await`` runs in this node's own control flow — nested
    function / lambda bodies are skipped (their awaits run later, elsewhere)."""

    def __init__(self) -> None:
        self.found = False

    def visit_Await(self, node: ast.Await) -> None:  # noqa: N802
        self.found = True

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        pass  # do not descend

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        pass  # do not descend

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        pass  # do not descend


def _body_has_await(node: ast.With) -> bool:
    finder = _AwaitFinder()
    for stmt in node.body:
        finder.visit(stmt)
        if finder.found:
            return True
    return False


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCAN_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if any(part in rel for part in _EXCLUDE_PARTS):
                continue
            files.append(path)
    return files


def _scan() -> dict[str, int]:
    """Map ``'<relpath>:<lineno>'`` -> lineno for each offending ``with``."""
    hits: dict[str, int] = {}
    for path in _iter_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and _is_lock_with(node) and _body_has_await(node):
                hits[f"{rel}:{node.lineno}"] = node.lineno
    return hits


def _load_baseline() -> set[str]:
    if not _BASELINE_PATH.is_file():
        return set()
    out: set[str] = set()
    for raw in _BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.add(stripped)
    return out


def _write_baseline(hits: dict[str, int]) -> None:
    lines = [
        "# `await` held across a synchronous lock, captured by",
        "# tools/lint/async_lock_check.py. Each line is '<relpath>:<lineno>'.",
        "# Moved the await outside the lock? Delete its line to lock in the fix.",
        "# Regenerate with --write-baseline.",
        "",
    ]
    lines.extend(sorted(hits.keys()))
    _BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _audit(strict: bool) -> int:
    hits = _scan()
    baseline = _load_baseline()
    hit_keys = set(hits.keys())

    new_hits = sorted(hit_keys - baseline)
    stale = sorted(baseline - hit_keys)

    if not new_hits and not stale:
        print(f"OK · {len(baseline)} baseline await-under-lock site(s) unchanged")
        return 0

    if stale:
        print(f"{len(stale)} baseline await-under-lock site(s) no longer present (fixed):")
        for entry in stale:
            print(f"  STALE  {entry}")
        print(
            "\nRemove them from "
            f"{_BASELINE_PATH.relative_to(REPO_ROOT).as_posix()} to lock in the fix."
        )

    if new_hits:
        print(f"\n{len(new_hits)} NEW await held across a synchronous lock:")
        for entry in new_hits:
            print(f"  NEW  {entry}")
        print(
            "\nHolding a threading.Lock/RLock across `await` suspends the\n"
            "coroutine while the lock is held — other tasks block on it and\n"
            "the event loop can deadlock. Fix one of:\n"
            "  1. Mutate under the lock, capture what you need into locals,\n"
            "     release, then `await` (broadcast/send) outside the lock, or\n"
            "  2. If the lock is genuinely an asyncio.Lock, use `async with`\n"
            "     (which this audit never flags), or\n"
            "  3. If this site is genuinely intended and safe, add it to\n"
            f"     {_BASELINE_PATH.relative_to(REPO_ROOT).as_posix()} via\n"
            "     --write-baseline and explain it in review."
        )

    if strict and (new_hits or stale):
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 on new/stale")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="overwrite the baseline with the current findings",
    )
    args = parser.parse_args()

    if args.write_baseline:
        hits = _scan()
        _write_baseline(hits)
        print(f"Wrote {len(hits)} entries to {_BASELINE_PATH.relative_to(REPO_ROOT).as_posix()}")
        return 0

    return _audit(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
