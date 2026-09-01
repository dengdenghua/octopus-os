"""Batch rewrite imports for the echo-agent rename refactor.

Replaces all references to old module paths with new ones across .py, .md, .yaml,
.yml, .json, .toml, .cfg, .txt files in the repo.

Safe: only does literal string replacement on full module paths (so `runtime.core.graph_runtime.foo`
becomes `runtime.core.graph_runtime.foo`, but a string literal "ganglia" in unrelated
context is NOT touched because the prefix `runtime.core.` is required).
"""
from __future__ import annotations

import sys
from pathlib import Path

RENAMES = {
    "runtime.core.graph_runtime": "runtime.core.graph_runtime",
    "runtime.core.hearts": "runtime.core.hearts",
    "runtime.core.nerves": "runtime.core.nerves",
    "runtime.core.nerves.reflex": "runtime.core.nerves.reflex",
    "runtime.execution.tool_engine": "runtime.execution.tool_engine",
    "runtime.sensing.model_router": "runtime.sensing.model_router",
    "runtime.sensing.gateway": "runtime.sensing.gateway",
    "runtime.sensing.server": "runtime.sensing.server",
    "runtime.sensing.normalize": "runtime.sensing.normalize",
    "runtime.safety.auth": "runtime.safety.auth",
    "runtime.safety.recovery": "runtime.safety.recovery",
    "runtime.safety.experiments": "runtime.safety.experiments",
    "runtime.safety.budget_breaker": "runtime.safety.budget_breaker",
    "runtime.safety.validation": "runtime.safety.validation",
    "runtime.memory.journal": "runtime.memory.journal",
}

# Sort longest first so prefixes don't collide (none do here, but defensive)
ORDERED = sorted(RENAMES.items(), key=lambda kv: -len(kv[0]))

EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".cfg", ".txt", ".ini", ".mermaid"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist",
             ".qoder", ".codex-logs", ".pytest_cache", ".ruff_cache", "echo_agent.egg-info"}


def should_process(path: Path) -> bool:
    if path.suffix.lower() not in EXTENSIONS:
        return False
    parts = set(path.parts)
    return not (parts & SKIP_DIRS)


def rewrite_file(path: Path) -> int:
    """Returns number of replacements made."""
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0
    original = content
    total = 0
    for old, new in ORDERED:
        count = content.count(old)
        if count:
            content = content.replace(old, new)
            total += count
    if content != original:
        path.write_text(content, encoding="utf-8")
    return total


def main(root: str) -> None:
    root_path = Path(root)
    files_changed = 0
    total_replacements = 0
    for p in root_path.rglob("*"):
        if not p.is_file():
            continue
        if not should_process(p):
            continue
        n = rewrite_file(p)
        if n:
            files_changed += 1
            total_replacements += n
            print(f"{n:5d}  {p.relative_to(root_path)}")
    print(f"\nDone: {files_changed} files, {total_replacements} replacements")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
