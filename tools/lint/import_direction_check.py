"""Forbid NEW cross-package imports that point the wrong way.

The review found the eight ``runtime/`` sub-packages form a near
fully-connected import graph: the "layering" only exists in directory
names. Untangling it all is multi-day work, so — like god_file_check —
we don't block on the existing debt. We DO stop it growing:

  * ``import_direction_baseline.txt`` captures today's violations.
  * Each run reports any forbidden edge that's NOT on the baseline
    (= a new wrong-way import → fails under --strict).
  * Baseline entries that no longer exist (someone fixed the edge) are
    reported as STALE so the win gets locked in by removing the line.

The "expected direction" we enforce (everything else is allowed):

  * the web / router layer ``sensing.gateway`` must be a LEAF — only
    ``sensing`` itself and ``platform`` (the FastAPI composition root
    that mounts the routers) may import it. A kernel module reaching
    into ``sensing.gateway`` (e.g. core/nerves/reflex/workflow_delegate)
    is the canonical wrong-way edge.
  * ``core`` / ``safety`` / ``memory`` / ``protocol`` must not depend on
    ``sensing`` at all (the kernel and the safety/memory/protocol layers
    sit below sensing). ``platform.models`` is the shared primitives
    base and is always allowed.

Imports inside ``if TYPE_CHECKING:`` blocks are exempt (they carry no
runtime dependency). Lazy imports inside function bodies are NOT exempt —
they are real direction-debt, just deferred; counting them is what keeps
the ratchet meaningful (only ~11 of the violations are module-level).

Run::

    python tools/lint/import_direction_check.py            # report
    python tools/lint/import_direction_check.py --strict   # exit 1 on new/stale
    python tools/lint/import_direction_check.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_PATH = REPO_ROOT / "tools" / "lint" / "import_direction_baseline.txt"
_RUNTIME = REPO_ROOT / "runtime"

# Skip generated / vendored / scaffolded / fixture code.
_EXCLUDE_PARTS: tuple[str, ...] = (
    "all_skills",
    "__pycache__",
    "tools/lint/fixtures",
)

# Forbidden edges: (source package, forbidden target-module prefix).
# ``platform`` is deliberately absent as a source — platform/ui/app.py is
# the composition root and legitimately mounts gateway routers.
_FORBIDDEN_EDGES: tuple[tuple[str, str], ...] = (
    ("core", "sensing"),
    ("safety", "sensing"),
    ("memory", "sensing"),
    ("protocol", "sensing"),
    ("execution", "sensing.gateway"),
    ("adapters", "sensing.gateway"),
    ("tentacle", "sensing.gateway"),
)

# Always-allowed target prefix — the shared primitives base used by all
# eight packages. A forbidden edge never fires when the target is here.
_ALWAYS_ALLOWED_PREFIXES: tuple[str, ...] = ("platform.models",)


def _src_pkg(path: Path) -> str | None:
    try:
        parts = path.relative_to(_RUNTIME).parts
    except ValueError:
        return None
    return parts[0] if parts else None


def _typecheck_lines(tree: ast.AST) -> set[int]:
    """Line numbers covered by ``if TYPE_CHECKING:`` blocks (exempt)."""
    exempt: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ""
        try:
            test_src = ast.unparse(node.test)
        except (ValueError, AttributeError):
            test_src = ""
        if "TYPE_CHECKING" not in test_src:
            continue
        for child in ast.walk(node):
            lineno = getattr(child, "lineno", None)
            if isinstance(lineno, int):
                exempt.add(lineno)
    return exempt


def _imported_modules(node: ast.AST) -> list[tuple[str, int]]:
    """(module, lineno) for absolute imports under this node."""
    out: list[tuple[str, int]] = []
    if isinstance(node, ast.ImportFrom):
        if node.level == 0 and node.module:
            out.append((node.module, node.lineno))
    elif isinstance(node, ast.Import):
        for alias in node.names:
            out.append((alias.name, node.lineno))
    return out


def _normalize(module: str) -> str | None:
    """``runtime.sensing.gateway.x`` -> ``sensing.gateway.x``; else None."""
    if module == "runtime":
        return None
    if module.startswith("runtime."):
        return module[len("runtime.") :]
    return None


def _violation(src: str, target: str) -> str | None:
    if any(target == p or target.startswith(p + ".") for p in _ALWAYS_ALLOWED_PREFIXES):
        return None
    for edge_src, edge_dst in _FORBIDDEN_EDGES:
        if src != edge_src:
            continue
        if target == edge_dst or target.startswith(edge_dst + "."):
            return f"{edge_src}->{edge_dst}"
    return None


def _scan() -> dict[str, tuple[str, int]]:
    """Map ``"<relpath>\t<target_module>" -> (rule_id, lineno)``."""
    hits: dict[str, tuple[str, int]] = {}
    for p in _RUNTIME.rglob("*.py"):
        rel_str = str(p).replace("\\", "/")
        if any(part in rel_str for part in _EXCLUDE_PARTS):
            continue
        src = _src_pkg(p)
        if src is None:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        exempt = _typecheck_lines(tree)
        rel = p.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            for module, lineno in _imported_modules(node):
                if lineno in exempt:
                    continue
                target = _normalize(module)
                if target is None:
                    continue
                rule = _violation(src, target)
                if rule is None:
                    continue
                hits[f"{rel}\t{target}"] = (rule, lineno)
    return hits


def _load_baseline() -> set[str]:
    if not _BASELINE_PATH.is_file():
        return set()
    out: set[str] = set()
    for raw in _BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.add(line)
    return out


def _write_baseline(hits: dict[str, tuple[str, int]]) -> None:
    lines = [
        "# Wrong-way cross-package imports, captured by",
        "# tools/lint/import_direction_check.py. Each line is",
        "# '<relpath>\\t<target_module>'. Fixed an edge? Delete its line so",
        "# the audit locks in the win. Regenerate with --write-baseline.",
        "",
    ]
    lines.extend(sorted(hits.keys()))
    _BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _audit(strict: bool) -> int:
    hits = _scan()
    baseline = _load_baseline()
    hit_keys = set(hits.keys())

    new_edges = sorted(hit_keys - baseline)
    stale = sorted(baseline - hit_keys)

    if not new_edges and not stale:
        print(f"OK · {len(baseline)} baseline wrong-way imports unchanged")
        return 0

    if stale:
        print(f"{len(stale)} baseline edge(s) no longer present (fixed):")
        for entry in stale:
            print(f"  STALE  {entry.replace(chr(9), '  ->  ')}")
        print(
            "\nRemove them from "
            f"{_BASELINE_PATH.relative_to(REPO_ROOT).as_posix()} to lock in "
            "the fix."
        )

    if new_edges:
        print(f"\n{len(new_edges)} NEW wrong-way import(s):")
        for entry in new_edges:
            rule, lineno = hits[entry]
            relpath, target = entry.split("\t", 1)
            print(f"  NEW  [{rule}]  {relpath}:{lineno}  imports  {target}")
        print(
            "\nThe target sits above the importing package in the intended\n"
            "layering. Fix one of:\n"
            "  1. Depend the other way / move the shared type down into\n"
            "     platform.models (the allowed base), or\n"
            "  2. If this edge is genuinely intended, add it to\n"
            f"     {_BASELINE_PATH.relative_to(REPO_ROOT).as_posix()} via\n"
            "     --write-baseline and explain it in review.\n"
            "  (A lazy import inside a function body is NOT a way out — it\n"
            "   still counts as a direction violation.)"
        )

    if strict and (new_edges or stale):
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--strict", action="store_true", help="exit 1 on new wrong-way imports or stale baseline"
    )
    p.add_argument(
        "--write-baseline",
        action="store_true",
        help="snapshot current violations to the baseline file",
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
