"""Phase B: rename Mantle -> Backend / Box -> Sandbox class names.

This script does WORD-LEVEL identifier replacement across .py + docs files.
Files / paths that were already renamed by Phase A's directory move are
left alone (those are caught separately by file rename).

Skips build/ (setuptools artifact) which still has copies of the old
sensing/mantle/ tree.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Order matters: longest-first so e.g. "Sandbox" is replaced before "Mantle".
RENAMES = [
    # Box family — instances of an isolated execution environment
    ("Sandbox",          "Sandbox"),
    ("DockerSandbox",          "DockerSandbox"),
    ("K8sSandbox",             "K8sSandbox"),
    ("SshSandbox",             "SshSandbox"),
    ("SubprocessSandbox",      "SubprocessSandbox"),
    # Backend family — server backend providers
    ("LocalBackend",        "LocalBackend"),
    ("DockerBackend",       "DockerBackend"),
    ("K8sBackend",          "K8sBackend"),
    ("SshBackend",          "SshBackend"),
    ("SubprocessBackend",   "SubprocessBackend"),
    # Audit / errors
    ("BackendAudit",        "BackendAudit"),
    # SensorEvent → SensorEvent (5 files)
    ("SensorEvent",          "SensorEvent"),
]

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
    for old, new in RENAMES:
        # Whole-word match — protects e.g. "Mantle" inside "DockerBackend"
        # from being replaced first when handling the bare "Mantle" base.
        # Since we don't currently rename bare "Mantle", \b is mostly defensive.
        pattern = r"\b" + re.escape(old) + r"\b"
        new_content, n = re.subn(pattern, new, content)
        if n:
            content = new_content
            total += n
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
            print(f"{n:5d}  {p.relative_to(root_path)}")
    print(f"\nDone: {files} files, {total} replacements")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
