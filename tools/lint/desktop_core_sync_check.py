"""Check pyproject extras vs Electron bootstrap runtime dep lists stay in sync."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
ELECTRON = REPO_ROOT / "frontend" / "electron" / "backend-runtime.cjs"

_STRLIT_RE = re.compile(r'"([^"]*)"')


def _norm(spec: str) -> str:
    return re.sub(r"\s+", "", spec.strip())


def _read_toml_extra_deps() -> dict[str, list[str]]:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    extras = data.get("project", {}).get("optional-dependencies", {})
    return {
        name: [_norm(dep) for dep in deps]
        for name, deps in extras.items()
        if isinstance(deps, list)
    }


def _extract_strings(body: str) -> list[str]:
    return [_norm(m.group(1)) for m in _STRLIT_RE.finditer(body)]


def _read_electron_deps() -> tuple[list[str], dict[str, list[str]]]:
    text = ELECTRON.read_text(encoding="utf-8")
    core: list[str] = []
    groups: dict[str, list[str]] = {}

    m = re.search(r"const\s+CORE_DEPS\s*=\s*\[(.*?)\];", text, re.S)
    if m:
        core = _extract_strings(m.group(1))

    m = re.search(r"const\s+OPTIONAL_GROUPS\s*=\s*\{(.*?)\};", text, re.S)
    if m:
        body = m.group(1)
        # Keys may be bare (desktop, vision) or quoted ("code-intel"). Match
        # both, then slice each group body by the next key position so
        # bracket-heavy specs like pyjwt[crypto] don't truncate the body.
        keys = list(re.finditer(r"(?:\"?)([\w-]+)(?:\"?)\s*:\s*\[", body))
        for i, km in enumerate(keys):
            start = km.end()
            end = keys[i + 1].start() if i + 1 < len(keys) else len(body)
            groups[km.group(1)] = _extract_strings(body[start:end])
    return core, groups


def _fmt_deps(deps: list[str]) -> str:
    return "\n    ".join(sorted(deps)) if deps else "(empty)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Check desktop-core/optional-group sync")
    ap.add_argument("--strict", action="store_true", help="fail on drift")
    args = ap.parse_args()

    if not PYPROJECT.exists() or not ELECTRON.exists():
        print("desktop_core_sync_check: missing source files", file=sys.stderr)
        return 1 if args.strict else 0

    py = _read_toml_extra_deps()
    core, groups = _read_electron_deps()
    errors: list[str] = []
    warnings: list[str] = []

    want_core = sorted(py.get("desktop-core", []))
    got_core = sorted(core)
    if want_core != got_core:
        errors.append(
            "CORE_DEPS != pyproject desktop-core\n  pyproject:\n    "
            + _fmt_deps(want_core)
            + "\n  electron:\n    "
            + _fmt_deps(got_core)
        )

    for group, deps in sorted(groups.items()):
        want = py.get(group)
        if want is None:
            errors.append(f"Electron optional group '{group}' missing from pyproject")
            continue
        want_n = sorted(want)
        got_n = sorted(deps)
        if want_n != got_n:
            warnings.append(
                f"OPTIONAL_GROUPS['{group}'] != pyproject [{group}]\n  pyproject:\n    "
                + _fmt_deps(want_n)
                + "\n  electron:\n    "
                + _fmt_deps(got_n)
            )

    if not errors and not warnings:
        print("desktop_core_sync_check: ok — core + optional groups in sync")
        return 0

    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print(
        f"\n{len(errors)} error(s), {len(warnings)} warning(s) · "
        "re-run with --strict to fail on drift",
        file=sys.stderr,
    )
    return 1 if (args.strict and (errors or warnings)) else (1 if errors else 0)


if __name__ == "__main__":
    sys.exit(main())
