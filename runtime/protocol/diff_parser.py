"""Unified-diff → structured :class:`FileHunk` decomposition.

The ``runtime.tools`` / skills layer produces edits as unified diff
strings (``--- a/path\\n+++ b/path\\n@@ -a,b +c,d @@\\n ...``). The
realtime protocol wants per-hunk items so the UI can render inline
diff with per-hunk accept/reject. This module is the adapter.

Kept intentionally small — no diff-matching, no rename detection,
no renumbering on partial accepts. That belongs downstream in the
apply step, not in the parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from runtime.protocol.items import FileChange, FileHunk

_HUNK_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_lines>\d+))?"
    r"\s+\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))?\s+@@",
)


@dataclass(frozen=True)
class _HunkDraft:
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    body: str


def _infer_op(
    has_old: bool,
    has_new: bool,
    old_start: int,
) -> Literal["create", "update", "delete"]:
    if not has_old or old_start == 0:
        return "create"
    if not has_new:
        return "delete"
    return "update"


def parse_unified_diff(diff: str) -> list[FileChange]:
    """Decompose a unified-diff string into one ``FileChange`` per file.

    Accepts multi-file diffs (sequential ``--- / +++`` blocks). A single
    ``+++`` without ``---`` is tolerated for "new file" creation diffs
    that some tools emit in that shorthand.
    """
    if not diff.strip():
        return []

    lines = diff.splitlines(keepends=True)
    i = 0
    out: list[FileChange] = []
    current_path: str | None = None
    current_op: Literal["create", "update", "delete"] | None = None
    current_hunks: list[_HunkDraft] = []
    current_body: list[str] = []
    current_header: _HunkDraft | None = None

    def flush_file() -> None:
        nonlocal current_path, current_op, current_hunks, current_header, current_body
        if current_header is not None:
            current_hunks.append(
                _HunkDraft(
                    old_start=current_header.old_start,
                    old_lines=current_header.old_lines,
                    new_start=current_header.new_start,
                    new_lines=current_header.new_lines,
                    body="".join(current_body),
                )
            )
            current_header = None
            current_body = []
        if current_path is None or current_op is None:
            current_hunks = []
            return
        out.append(
            FileChange(
                path=current_path,
                op=current_op,
                diff="".join(_rebuild_diff(current_path, current_op, current_hunks)),
                hunks=[
                    FileHunk(
                        old_start=h.old_start,
                        old_lines=h.old_lines,
                        new_start=h.new_start,
                        new_lines=h.new_lines,
                        body=h.body,
                    )
                    for h in current_hunks
                ],
            )
        )
        current_path = None
        current_op = None
        current_hunks = []

    pending_old_path: str | None = None
    pending_new_path: str | None = None

    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            # New file entry — flush previous.
            flush_file()
            pending_old_path = _strip_diff_prefix(line[4:].strip())
            pending_new_path = None
            i += 1
            continue
        if line.startswith("+++ "):
            pending_new_path = _strip_diff_prefix(line[4:].strip())
            current_path = pending_new_path or pending_old_path
            current_op = _infer_op(
                has_old=bool(pending_old_path) and pending_old_path != "/dev/null",
                has_new=bool(pending_new_path) and pending_new_path != "/dev/null",
                old_start=1,
            )
            if pending_new_path == "/dev/null":
                current_path = pending_old_path
                current_op = "delete"
            i += 1
            continue
        m = _HUNK_RE.match(line)
        if m:
            if current_header is not None:
                current_hunks.append(
                    _HunkDraft(
                        old_start=current_header.old_start,
                        old_lines=current_header.old_lines,
                        new_start=current_header.new_start,
                        new_lines=current_header.new_lines,
                        body="".join(current_body),
                    )
                )
                current_body = []
            current_header = _HunkDraft(
                old_start=int(m.group("old_start")),
                old_lines=int(m.group("old_lines") or 1),
                new_start=int(m.group("new_start")),
                new_lines=int(m.group("new_lines") or 1),
                body="",
            )
            # Fix create op: ``--- /dev/null`` path is held in
            # pending_old_path; we already inferred the op above, but
            # an old_start == 0 hunk is a strong create signal.
            if current_header.old_start == 0 and current_op != "delete":
                current_op = "create"
            i += 1
            continue
        if current_header is not None:  # noqa: SIM102
            # Hunk body: lines starting with ' ', '+', '-', or '\\'
            # (the no-newline marker). Anything else aborts the body.
            if line and line[0] in (" ", "+", "-", "\\"):
                current_body.append(line)
                i += 1
                continue
            # Body ended implicitly; fall through without consuming.
        i += 1

    flush_file()
    return out


def _strip_diff_prefix(path: str) -> str:
    """Remove the conventional ``a/`` or ``b/`` prefix from a diff
    header path. Some tools emit bare paths; handle both."""
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _rebuild_diff(
    path: str,
    op: Literal["create", "update", "delete"],
    hunks: list[_HunkDraft],
) -> list[str]:
    """Reassemble a unified diff for round-trip ``revert-diff`` use.

    Uses the same ``a/`` / ``b/`` convention the apply step expects.
    ``delete`` → ``+++ /dev/null``; ``create`` → ``--- /dev/null``.
    """
    old_path = "/dev/null" if op == "create" else f"a/{path}"
    new_path = "/dev/null" if op == "delete" else f"b/{path}"
    lines: list[str] = [f"--- {old_path}\n", f"+++ {new_path}\n"]
    for h in hunks:
        lines.append(f"@@ -{h.old_start},{h.old_lines} +{h.new_start},{h.new_lines} @@\n")
        if h.body and not h.body.endswith("\n"):
            lines.append(h.body + "\n")
        else:
            lines.append(h.body)
    return lines


__all__ = ["parse_unified_diff"]
