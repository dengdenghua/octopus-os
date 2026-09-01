"""Reverse rename: undo hearts/nerves changes from Phase A."""
from __future__ import annotations

import sys
from pathlib import Path

REVERSES = {
    "runtime.core.hearts": "runtime.core.hearts",
    "runtime.core.nerves": "runtime.core.nerves",
}
ORDERED = sorted(REVERSES.items(), key=lambda kv: -len(kv[0]))
EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".cfg", ".txt", ".ini", ".mermaid"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist",
             ".qoder", ".codex-logs", ".pytest_cache", ".ruff_cache", "echo_agent.egg-info"}

def should_process(path: Path) -> bool:
    if path.suffix.lower() not in EXTENSIONS:
        return False
    return not (set(path.parts) & SKIP_DIRS)

def rewrite_file(path: Path) -> int:
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
    files = 0
    total = 0
    for p in root_path.rglob("*"):
        if not p.is_file() or not should_process(p):
            continue
        n = rewrite_file(p)
        if n:
            files += 1
            total += n
    print(f"FQN: {files} files, {total} replacements")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
