"""Phase 2: rewrite bare submodule names in `from runtime.X import Y, Z` style.

The first script handled fully-qualified `runtime.X.Y` paths. This handles the
`from runtime.PARENT import OLD_NAME` form where OLD_NAME alone is the bare submodule.

Approach: regex-rewrite `from runtime.PARENT import (...)` blocks (single-line or
parenthesized multi-line), substituting old names for new ones in the imported list.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Map: parent_path -> {old_bare_name: new_bare_name}
PARENT_RENAMES = {
    "runtime.core": {
        "ganglia": "graph_runtime",
        "hearts": "consensus",
        "nerves": "event_bus",
        "spinal_cord": "fast_path",
    },
    "runtime.execution": {
        "beak": "tool_engine",
    },
    "runtime.sensing": {
        "eyes": "model_router",
        "siphon": "gateway",
        "mantle": "server",
        "skin": "normalize",
    },
    "runtime.safety": {
        "immunity": "auth",
        "regeneration": "recovery",
        "camouflage": "experiments",
        "ink": "budget_breaker",
        "constitution": "validation",
    },
    "runtime.memory": {
        "genome": "journal",
    },
}

EXTENSIONS = {".py"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist",
             ".qoder", ".codex-logs", ".pytest_cache", ".ruff_cache", "echo_agent.egg-info"}


def should_process(path: Path) -> bool:
    if path.suffix.lower() not in EXTENSIONS:
        return False
    parts = set(path.parts)
    return not (parts & SKIP_DIRS)


def rewrite_file(path: Path) -> int:
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0
    original = content
    replacements = 0

    for parent, name_map in PARENT_RENAMES.items():
        # Pattern matches both:
        #   from runtime.X import a, b, c
        #   from runtime.X import (a, b, c)
        # captures the import list lazily up to closing newline or paren
        # We substitute inside the captured list.
        pattern_paren = re.compile(
            r"(from\s+" + re.escape(parent) + r"\s+import\s*\()([^)]*)(\))",
            re.MULTILINE,
        )
        pattern_bare = re.compile(
            r"(from\s+" + re.escape(parent) + r"\s+import\s+)([^\n(]+)",
        )

        def replace_names(match: re.Match, _name_map: dict = name_map) -> str:
            head, body = match.group(1), match.group(2)
            tail = match.group(3) if match.lastindex == 3 else ""
            new_body = body
            for old, new in _name_map.items():
                # Replace only whole-word identifiers
                new_body, n = re.subn(r"\b" + re.escape(old) + r"\b", new, new_body)
                nonlocal replacements
                replacements += n
            return head + new_body + tail

        # Apply paren form first (multi-line), then bare form
        content = pattern_paren.sub(replace_names, content)
        content = pattern_bare.sub(replace_names, content)

    if content != original:
        path.write_text(content, encoding="utf-8")
        return replacements
    return 0


def main(root: str) -> None:
    root_path = Path(root)
    files_changed = 0
    total = 0
    for p in root_path.rglob("*"):
        if not p.is_file() or not should_process(p):
            continue
        n = rewrite_file(p)
        if n:
            files_changed += 1
            total += n
            print(f"{n:5d}  {p.relative_to(root_path)}")
    print(f"\nDone: {files_changed} files, {total} bare-name replacements")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
