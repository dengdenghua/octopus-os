"""Unified-diff parsing / reverse-apply helpers for the filesystem router.

Extracted from ``fs_router.py`` (god-file reduction). Used by the
``/api/fs/revert-diff`` endpoint and, via the ``fs_router`` re-exports, by
``realtime_thread_ops`` for hunk rejection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@",
)


class _DiffFormatError(ValueError):
    pass


class _DiffApplyConflict(RuntimeError):
    pass


@dataclass
class _ParsedDiffLine:
    marker: str
    content: str


@dataclass
class _ParsedDiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[_ParsedDiffLine]


def _parse_unified_diff(diff_text: str) -> list[_ParsedDiffHunk]:
    if not diff_text.strip():
        raise _DiffFormatError("diff is required")
    if "\n... (truncated " in diff_text:
        raise _DiffFormatError("truncated diffs cannot be reverted safely")

    hunks: list[_ParsedDiffHunk] = []
    current: _ParsedDiffHunk | None = None
    lines = diff_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    for raw_line in lines:
        if raw_line.startswith("@@"):
            if current is not None:
                hunks.append(current)
            match = _HUNK_HEADER_RE.match(raw_line)
            if not match:
                raise _DiffFormatError(f"invalid hunk header: {raw_line}")
            current = _ParsedDiffHunk(
                old_start=int(match.group("old_start")),
                old_count=int(match.group("old_count") or "1"),
                new_start=int(match.group("new_start")),
                new_count=int(match.group("new_count") or "1"),
                lines=[],
            )
            continue

        if current is None:
            continue
        if raw_line.startswith("\\ No newline at end of file"):
            continue
        if raw_line == "":
            continue

        marker = raw_line[:1]
        if marker not in {" ", "+", "-"}:
            raise _DiffFormatError(f"invalid diff line: {raw_line}")
        current.lines.append(_ParsedDiffLine(marker=marker, content=raw_line[1:]))

    if current is not None:
        hunks.append(current)
    if not hunks:
        raise _DiffFormatError("diff contains no hunks")
    return hunks


def _content_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").splitlines()


def _join_content_lines(lines: list[str], *, trailing_newline: bool) -> str:
    if not lines:
        return ""
    content = "\n".join(lines)
    if trailing_newline:
        content += "\n"
    return content


def _preferred_new_index(hunk: _ParsedDiffHunk) -> int:
    if hunk.new_count == 0:
        return max(hunk.new_start, 0)
    return max(hunk.new_start - 1, 0)


def _find_line_segment(
    lines: list[str],
    segment: list[str],
    preferred_index: int,
) -> int:
    if not segment:
        if 0 <= preferred_index <= len(lines):
            return preferred_index
        raise _DiffApplyConflict("empty hunk location is outside the current file")

    end = len(lines) - len(segment)
    if (
        0 <= preferred_index <= end
        and lines[preferred_index : preferred_index + len(segment)] == segment
    ):
        return preferred_index

    matches: list[int] = []
    for index in range(max(end + 1, 0)):
        if lines[index : index + len(segment)] == segment:
            matches.append(index)
            if len(matches) > 1:
                break
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise _DiffApplyConflict("hunk matches multiple locations in the current file")
    raise _DiffApplyConflict("hunk no longer matches the current file")


def _reverse_unified_diff(current_text: str, diff_text: str) -> str:
    hunks = _parse_unified_diff(diff_text)
    normalized = current_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = _content_lines(normalized)

    for hunk in reversed(hunks):
        new_segment = [line.content for line in hunk.lines if line.marker != "-"]
        old_segment = [line.content for line in hunk.lines if line.marker != "+"]
        index = _find_line_segment(
            lines,
            new_segment,
            _preferred_new_index(hunk),
        )
        lines[index : index + len(new_segment)] = old_segment

    trailing_newline = normalized.endswith("\n") or (not normalized and bool(lines))
    return _join_content_lines(lines, trailing_newline=trailing_newline)
