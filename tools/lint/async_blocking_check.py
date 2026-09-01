"""Forbid NEW blocking calls in async control flow.

A synchronous blocking call (``time.sleep``, a ``requests`` HTTP call,
``subprocess.run``, ``urllib.request.urlopen``, ``os.system``) inside an
``async def`` blocks the *whole event loop* for its duration — every other
coroutine on that loop stalls, not just the one that called it. Each has an
``await``-able alternative (``asyncio.sleep``, ``httpx.AsyncClient`` /
``aiohttp``, ``asyncio.create_subprocess_exec``) or, if it must stay sync,
belongs in a thread via ``run_in_executor`` / ``asyncio.to_thread`` (i.e. in
a plain ``def`` that the executor runs — which this audit does not flag).

Same event-loop-starvation family as ``async_lock_check`` (await held across
a sync lock); this one catches the dual: a sync call that should have been
awaited. The tree already obeys the rule, so the baseline is empty and this
is pure prevention.

  * ``async_blocking_baseline.txt`` captures today's violations (none).
  * Each run reports a blocking call in async flow NOT on the baseline
    (= a new event-loop staller → fails under ``--strict``).
  * Baseline entries that no longer exist are reported STALE.

A blocking call inside a *nested* plain ``def`` / ``lambda`` is NOT flagged:
that function may legitimately be handed to an executor. A nested
``async def`` is scanned on its own (so its body is checked there, not
double-counted via the enclosing one).

Run::

    python tools/lint/async_blocking_check.py            # report
    python tools/lint/async_blocking_check.py --strict   # exit 1 on new/stale
    python tools/lint/async_blocking_check.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_PATH = REPO_ROOT / "tools" / "lint" / "async_blocking_baseline.txt"

_SCAN_ROOTS: tuple[str, ...] = ("runtime", "tests")

_EXCLUDE_PARTS: tuple[str, ...] = (
    "all_skills",
    "__pycache__",
    "tools/lint/fixtures",
)

# Unambiguously blocking calls that stall the event loop and have an
# await-able (or executor-able) alternative. Matched by dotted call name,
# so ``import time; time.sleep()`` and ``from time import sleep`` differ —
# the bare-name form (``sleep(...)``) is intentionally NOT matched to avoid
# false positives on unrelated local ``sleep``/``run`` helpers; the
# module-qualified form is the one that's unambiguous.
_BLOCKING_CALLS: frozenset[str] = frozenset(
    {
        "time.sleep",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.patch",
        "requests.head",
        "requests.request",
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_output",
        "subprocess.check_call",
        "urllib.request.urlopen",
        "os.system",
    }
)


def _dotted_name(node: ast.expr) -> str | None:
    """``a.b.c`` for an Attribute/Name chain, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class _BlockingCallScan(ast.NodeVisitor):
    """Collect blocking calls in *this* coroutine's own control flow.
    Stops at every nested function boundary: a nested ``def``/``lambda`` may
    run in an executor, and a nested ``async def`` is scanned independently."""

    def __init__(self) -> None:
        self.hits: list[tuple[str, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        pass

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _dotted_name(node.func)
        if name in _BLOCKING_CALLS:
            self.hits.append((name, node.lineno))
        self.generic_visit(node)


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


def _scan() -> dict[str, str]:
    """Map ``'<relpath>:<lineno>'`` -> blocking call name."""
    hits: dict[str, str] = {}
    for path in _iter_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                scan = _BlockingCallScan()
                for stmt in node.body:
                    scan.visit(stmt)
                for name, lineno in scan.hits:
                    hits[f"{rel}:{lineno}"] = name
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


def _write_baseline(hits: dict[str, str]) -> None:
    lines = [
        "# Blocking calls inside async control flow, captured by",
        "# tools/lint/async_blocking_check.py. Each line is '<relpath>:<lineno>'.",
        "# Awaited it / moved it to an executor? Delete its line to lock in the fix.",
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
        print(f"OK · {len(baseline)} baseline blocking-in-async site(s) unchanged")
        return 0

    if stale:
        print(f"{len(stale)} baseline blocking-in-async site(s) no longer present (fixed):")
        for entry in stale:
            print(f"  STALE  {entry}")
        print(
            "\nRemove them from "
            f"{_BASELINE_PATH.relative_to(REPO_ROOT).as_posix()} to lock in the fix."
        )

    if new_hits:
        print(f"\n{len(new_hits)} NEW blocking call(s) in async control flow:")
        for entry in new_hits:
            print(f"  NEW  {entry}  ({hits[entry]})")
        print(
            "\nA synchronous blocking call inside `async def` stalls the whole\n"
            "event loop. Fix one of:\n"
            "  1. Use the await-able alternative (asyncio.sleep, an async HTTP\n"
            "     client, asyncio.create_subprocess_exec), or\n"
            "  2. Offload it to a thread: `await asyncio.to_thread(fn, ...)` /\n"
            "     `run_in_executor` (put the blocking work in a plain `def`), or\n"
            "  3. If genuinely safe here, add it to\n"
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
