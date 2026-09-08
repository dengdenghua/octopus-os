"""Repository-level source integrity checks for maintained Python scripts."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_maintained_root_python_scripts_are_valid_source() -> None:
    """Reject migration diagnostics or other non-Python text saved as scripts."""
    failures: list[str] = []
    for path in sorted((REPO_ROOT / "scripts").glob("*.py")):
        try:
            source = path.read_text(encoding="utf-8", errors="strict")
            ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            failures.append(f"{path.relative_to(REPO_ROOT)}: {exc}")

    assert not failures, "invalid maintained Python scripts:\n" + "\n".join(failures)
