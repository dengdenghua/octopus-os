"""Phase B follow-up: rename local 'mantle' identifiers to 'backend'.

Limited to source files under runtime/sensing/server/ and tests under
tests/test_*_backend.py — these are the only places where the old
biomimetic vocabulary survives as identifiers.

Whole-word match. Skips strings that *contain* mantle as a substring
of another identifier (none expected after Phase A).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TARGET_FILES = [
    "runtime/sensing/server/local.py",
    "runtime/sensing/server/docker.py",
    "runtime/sensing/server/k8s.py",
    "runtime/sensing/server/ssh.py",
    "runtime/sensing/server/subprocess_backend.py",
    "tests/test_subprocess_backend.py",
    "tests/test_docker_backend.py",
    "tests/test_k8s_backend.py",
    "tests/test_ssh_backend.py",
]

# Whole-word lowercase replacement
PATTERN = re.compile(r"\bmantle\b")
REPLACEMENT = "backend"


def rewrite_file(path: Path) -> int:
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0
    new_content, n = PATTERN.subn(REPLACEMENT, content)
    if n:
        path.write_text(new_content, encoding="utf-8")
    return n


def main(root: str) -> None:
    root_path = Path(root)
    files = 0
    total = 0
    for rel in TARGET_FILES:
        p = root_path / rel
        if not p.is_file():
            continue
        n = rewrite_file(p)
        if n:
            files += 1
            total += n
            print(f"{n:5d}  {rel}")
    print(f"\nDone: {files} files, {total} replacements")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
