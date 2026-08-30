"""Orphan-module audit · flag runtime modules that nobody imports.

Dormant code is a honesty-of-surface problem: a module exported but
never consumed reads as "a working feature" to a new reader, and rots
silently. This linter catches the NEXT one the moment it lands.

How it works
------------
  * Build the *consumed* set: every dotted module path imported anywhere
    under runtime/ AND its consumers (tests/, demos/, benchmarks/,
    tools/, scripts/). Both absolute (``runtime.a.b``) and relative
    (``from . import x`` / ``from ..y import z``) imports are resolved
    to absolute dotted paths.
  * A runtime module whose dotted path is in NO consumer is an orphan.
  * ``orphan_module_baseline.txt`` captures today's orphans. A run
    reports any orphan NOT on the baseline (a newly-stranded module),
    and STALE baseline entries (a module that got wired up or deleted —
    remove the line to lock the win).

Exemptions
----------
  * ``__init__.py`` / ``__main__.py`` — structural / process entry, not
    imported by name.
  * Console-script targets from pyproject ``[project.scripts]`` — called
    by external entry points, not by imports.
  * Generated/scaffold/fixture trees (all_skills, fixtures).

Limits (honest)
---------------
Static analysis can't see ``importlib`` / string-keyed registries /
plugin loaders. A module used only dynamically shows as an orphan and
must be baselined — which is fine: the baseline line then DOCUMENTS the
dynamic dependency. The point is the ratchet on NEW orphans, not a
perfect dead-code oracle.

Run::

    python tools/lint/orphan_module_check.py            # report
    python tools/lint/orphan_module_check.py --strict   # exit 1 on new
    python tools/lint/orphan_module_check.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_PATH = REPO_ROOT / "tools" / "lint" / "orphan_module_baseline.txt"
_RUNTIME = REPO_ROOT / "runtime"

# Trees scanned for CONSUMERS (imports). A runtime module used by any of
# these is not an orphan.
_CONSUMER_ROOTS: tuple[str, ...] = (
    "runtime",
    "tests",
    "demos",
    "benchmarks",
    "tools",
    "scripts",
)

# Two different exclusion purposes, two lists:
#
# CONSUMER scan must be near-complete — any file that imports a runtime
# module counts, including all_skills/__init__.py, the registration hub
# that imports the file-backed skill modules. Excluding it here would
# falsely strand every skill it registers.
_CONSUMER_EXCLUDE: tuple[str, ...] = (
    "__pycache__",
    "tools/lint/fixtures",
    # Downloaded SWE-bench repositories are evaluation inputs, not Echo
    # consumers. Parsing them adds tens of seconds and emits warnings from
    # upstream legacy syntax without improving the dependency graph.
    "benchmarks/results",
)

# CANDIDATE scan (what may be flagged AS an orphan) additionally skips
# bundled non-import ASSETS that merely live under runtime/:
# agent_market_sources/*/skills/*/scripts are skill scripts run as
# subprocesses, apks/ are mobile build artifacts — neither is an
# importable module, so "nobody imports them" is expected, not a smell.
_CANDIDATE_EXCLUDE: tuple[str, ...] = (
    *_CONSUMER_EXCLUDE,
    "/tests/",  # test modules are consumers, never production orphan candidates
    "all_skills",  # generated/scaffolded skill packages
    "agent_market_sources",  # bundled agent skill scripts (subprocess assets)
    "/apks/",  # mobile build artifacts
)


def _lazy_module_map() -> dict[str, str]:
    """Parse ``runtime/platform/__init__.py`` for ``_LAZY_MODULES``.

    PEP 562 ``__getattr__`` lazy loading means ``from runtime.platform
    import X`` actually loads ``_LAZY_MODULES[X]`` (e.g.
    ``runtime.platform.runtime_policy.feature_flags``), not
    ``runtime.platform.X``. The AST sees the latter and misses the real
    module path, falsely flagging it as an orphan. This maps the short
    name back to the real dotted path so dynamically-reached modules
    aren't stranded.
    """
    init_path = REPO_ROOT / "runtime" / "platform" / "__init__.py"
    if not init_path.is_file():
        return {}
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_LAZY_MODULES":  # noqa: SIM102 — distinct checks
                    if isinstance(node.value, ast.Dict):
                        out: dict[str, str] = {}
                        for key, val in zip(node.value.keys, node.value.values, strict=False):
                            if (
                                isinstance(key, ast.Constant)
                                and isinstance(key.value, str)
                                and isinstance(val, ast.Constant)
                                and isinstance(val.value, str)
                            ):
                                out[key.value] = val.value
                        return out
    return {}


def _dotted(path: Path) -> str | None:
    """``<repo>/runtime/a/b.py`` -> ``runtime.a.b``; package -> dir dotted."""
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return None
    parts = list(rel.parts)
    if not parts or not parts[-1].endswith(".py"):
        return None
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _resolve_relative(importer_dotted_pkg: str, level: int, module: str | None) -> str | None:
    """Resolve a relative import to an absolute dotted path.

    ``importer_dotted_pkg`` is the importing module's PACKAGE (its dotted
    path with the final component dropped for non-packages — callers pass
    the package). ``level`` is the number of leading dots.
    """
    base_parts = importer_dotted_pkg.split(".") if importer_dotted_pkg else []
    # level=1 → current package; level=2 → parent; etc.
    if level - 1 > len(base_parts):
        return None
    anchor = base_parts[: len(base_parts) - (level - 1)]
    if module:
        anchor = anchor + module.split(".")
    return ".".join(anchor) if anchor else None


def _package_of(module_dotted: str, is_pkg_init: bool) -> str:
    """The package a module's relative imports are anchored at."""
    if is_pkg_init:
        return module_dotted  # __init__ IS the package
    parts = module_dotted.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else ""


def _consumed_targets(path: Path) -> set[str]:
    """Absolute dotted module paths this file imports."""
    out: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return out
    self_dotted = _dotted(path)
    is_init = path.name == "__init__.py"
    pkg = _package_of(self_dotted, is_init) if self_dotted else ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("runtime"):
                    out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                base = _resolve_relative(pkg, node.level, node.module)
                if base and base.startswith("runtime"):
                    out.add(base)
                    for alias in node.names:
                        out.add(f"{base}.{alias.name}")
            elif node.module and node.module.startswith("runtime"):
                out.add(node.module)
                for alias in node.names:
                    out.add(f"{node.module}.{alias.name}")
    return out


def _script_entry_targets() -> set[str]:
    """Module paths named in pyproject [project.scripts] (entry points)."""
    targets: set[str] = set()
    pyproject = REPO_ROOT / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return targets
    in_scripts = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_scripts = line == "[project.scripts]"
            continue
        if in_scripts and "=" in line:
            rhs = line.split("=", 1)[1].strip().strip('"').strip("'")
            mod = rhs.split(":", 1)[0].strip()
            if mod.startswith("runtime"):
                targets.add(mod)
    return targets


def _excluded(path: Path, parts: tuple[str, ...]) -> bool:
    rel = str(path).replace("\\", "/")
    return any(part in rel for part in parts)


def _scan() -> set[str]:
    """Return relpaths of orphan runtime modules."""
    consumed: set[str] = set()
    for root_name in _CONSUMER_ROOTS:
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            if _excluded(p, _CONSUMER_EXCLUDE):
                continue
            consumed |= _consumed_targets(p)

    # PEP 562 lazy loading: ``from runtime.platform import X`` resolves
    # through ``__getattr__`` to ``_LAZY_MODULES[X]``, a different dotted
    # path than ``runtime.platform.X``. Map consumed short names back to
    # their real module paths so dynamically-reached modules aren't
    # falsely stranded. Only modules ACTUALLY imported (already in
    # ``consumed`` as ``runtime.platform.X``) get their real path added —
    # modules registered but never imported stay flagged as orphans.
    lazy_map = _lazy_module_map()
    if lazy_map:
        lazy_resolved: set[str] = set()
        for target in consumed:
            if target.startswith("runtime.platform."):
                short = target[len("runtime.platform.") :]
                if short in lazy_map:
                    lazy_resolved.add(lazy_map[short])
        consumed |= lazy_resolved

    exempt_entry = _script_entry_targets()

    orphans: set[str] = set()
    for p in _RUNTIME.rglob("*.py"):
        if _excluded(p, _CANDIDATE_EXCLUDE):
            continue
        if p.name in ("__init__.py", "__main__.py"):
            continue
        dotted = _dotted(p)
        if dotted is None:
            continue
        if dotted in consumed or dotted in exempt_entry:
            continue
        orphans.add(p.relative_to(REPO_ROOT).as_posix())
    return orphans


def _load_baseline() -> set[str]:
    if not _BASELINE_PATH.is_file():
        return set()
    out: set[str] = set()
    for raw in _BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        out.add(s)
    return out


def _write_baseline(orphans: set[str]) -> None:
    lines = [
        "# Runtime modules nobody imports, captured by",
        "# tools/lint/orphan_module_check.py. Each line is a relpath.",
        "# Wired one up or deleted it? Remove its line to lock the win.",
        "# A line for a module used only via importlib/registry documents",
        "# that dynamic dependency — keep it, it's not a bug.",
        "",
    ]
    lines.extend(sorted(orphans))
    _BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _audit(strict: bool) -> int:
    orphans = _scan()
    baseline = _load_baseline()

    new = sorted(orphans - baseline)
    stale = sorted(baseline - orphans)

    if not new and not stale:
        print(f"OK · {len(baseline)} baseline orphan module(s) unchanged")
        return 0

    if stale:
        print(f"{len(stale)} baseline orphan(s) no longer stranded (wired or deleted):")
        for entry in stale:
            print(f"  STALE  {entry}")
        print(
            f"\nRemove them from {_BASELINE_PATH.relative_to(REPO_ROOT).as_posix()} "
            "to lock in the win."
        )

    if new:
        print(f"\n{len(new)} NEW orphan module(s) — exported but nobody imports:")
        for entry in new:
            print(f"  NEW  {entry}")
        print(
            "\nA module no consumer imports is dead-on-arrival. Fix one of:\n"
            "  1. Wire it up (and add a test that exercises it), or\n"
            "  2. Delete it, or\n"
            "  3. If it's reached dynamically (importlib / a string-keyed\n"
            "     registry / a plugin loader), add it to\n"
            f"     {_BASELINE_PATH.relative_to(REPO_ROOT).as_posix()} via\n"
            "     --write-baseline so the dynamic dependency is documented."
        )

    if strict and (new or stale):
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--strict", action="store_true", help="exit 1 on new orphans or stale baseline")
    p.add_argument(
        "--write-baseline",
        action="store_true",
        help="snapshot current orphan modules to the baseline file",
    )
    args = p.parse_args()

    if args.write_baseline:
        orphans = _scan()
        _write_baseline(orphans)
        rel = _BASELINE_PATH.relative_to(REPO_ROOT).as_posix()
        print(f"wrote {len(orphans)} entries to {rel}")
        return 0

    return _audit(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
