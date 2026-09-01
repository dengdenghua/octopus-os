"""Seed a language server with the files a reference search must cover.

Some servers -- pyright among them -- answer ``textDocument/references``
only from documents the client has opened. Asking about a symbol with the
definition file alone open returns zero results, which reads exactly like
"nothing uses this" and is the most dangerous way for this integration to be
wrong: an agent acting on it would delete live code.

So the name is used the way name matching is actually good -- as a cheap,
over-inclusive candidate filter -- and the server still decides which
candidates are real references. Grep proposes, LSP disposes.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Opening a document costs analysis time, so the candidate set is capped.
# Ordered by grep, which yields no useful priority, hence a generous cap
# rather than a clever one.
MAX_CANDIDATE_FILES = 60
GREP_TIMEOUT_SECONDS = 20.0

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".mypy_cache"}
)


def identifier_at(path: Path, line: int, character: int) -> str | None:
    """Read the identifier under a 1-based line/character position."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if line < 1 or line > len(lines):
        return None
    text = lines[line - 1]
    index = character - 1
    if index < 0 or index >= len(text):
        return None
    for match in _IDENTIFIER.finditer(text):
        if match.start() <= index < match.end():
            return match.group(0)
    return None


def _ripgrep_files(name: str, root: Path, extensions: frozenset[str]) -> list[Path] | None:
    """Ask ripgrep for files containing ``name`` as a whole word.

    Returns ``None`` when rg is unavailable so the caller can fall back.
    """
    import shutil

    rg = shutil.which("rg")
    if not rg:
        return None
    cmd = [rg, "--files-with-matches", "--word-regexp", "--fixed-strings", name]
    for ext in sorted(extensions):
        cmd += ["--glob", f"*{ext}"]
    try:
        proc = subprocess.run(  # noqa: S603 - argv built from a literal + a validated identifier
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=GREP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # rg exits 1 for "no matches", which is a valid empty answer.
    if proc.returncode not in (0, 1):
        return None
    out: list[Path] = []
    for line in proc.stdout.splitlines():
        rel = line.strip()
        if rel:
            out.append(root / rel)
    return out


def _walk_files(name: str, root: Path, extensions: frozenset[str]) -> list[Path]:
    """Fallback scan when ripgrep is absent."""
    needle = name.encode("utf-8")
    found: list[Path] = []
    for path in root.rglob("*"):
        if len(found) >= MAX_CANDIDATE_FILES:
            break
        if path.suffix.lower() not in extensions or not path.is_file():
            continue
        if any(part in _SKIP_DIRS or part.startswith(".") for part in path.parts):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            if needle in path.read_bytes():
                found.append(path)
        except OSError:
            continue
    return found


def candidate_files(
    name: str,
    root: Path,
    extensions: frozenset[str],
    *,
    limit: int = MAX_CANDIDATE_FILES,
) -> tuple[list[Path], bool]:
    """Files that mention ``name``, plus whether the list was truncated.

    The truncation flag is propagated to the caller so a capped search can say
    so rather than presenting a partial answer as complete.
    """
    if not name or not _IDENTIFIER.fullmatch(name):
        return [], False
    files = _ripgrep_files(name, root, extensions)
    if files is None:
        files = _walk_files(name, root, extensions)
    truncated = len(files) > limit
    return files[:limit], truncated
