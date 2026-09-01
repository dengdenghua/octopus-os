"""Tool-event → item/description builders for the realtime bridge.

Extracted from ``realtime_event_bridge.py``: pure functions that turn
``tool_start`` / ``tool_end`` react events into first-class
``FileChangeItem`` / ``VerificationItem`` records, plus the narrative
helpers that extract a short human-authored description safe for main
chat, and the capped-append helpers that bound aggregated output and
stream content.

These have no dependency on ``_ReactBridgeState`` — they operate on
plain dicts and protocol models — so they live here to keep the state
class focused on lifecycle / coalescing.
"""

from __future__ import annotations

import re
from typing import Any

from runtime.protocol import (
    CommandExecutionItem,
    FileChange,
    FileChangeItem,
    FileHunk,
    ItemStatus,
    VerificationItem,
)
from runtime.protocol.diff_parser import parse_unified_diff
from runtime.protocol.items import diff_is_truncated
from runtime.protocol.text_limits import (
    MAX_AGGREGATED_OUTPUT,
    MAX_STREAM_ITEM_CONTENT,
    OUTPUT_TRUNCATION_MARK,
    STREAM_CONTENT_TRUNCATION_MARK,
    append_capped_text,
)


# Hard cap on a single command item's retained output. The per-delta
# stream (ITEM_COMMAND_OUTPUT_DELTA) still carries every chunk live, so the
# user's view is unaffected — this only bounds the *accumulated* buffer that
# gets re-serialized whole into workbench snapshots and the turn/completed
# frame. Without it a runaway command (a stress test, a verbose build) grows
# aggregated_output without limit until that frame exceeds the realtime WS
# 16 MiB message ceiling and the socket is dropped with code 1009 — which
# also took down mid-run backends. 256 KiB is far more than any rendered log
# needs and keeps a whole turn's items well under the frame limit.
def _append_capped_output(existing: str, delta: str) -> str:
    """Append ``delta`` to ``existing`` but never grow past the cap.

    Once the cap is reached the buffer is frozen (the live delta stream
    still delivers subsequent chunks), so this is also O(cap) per delta
    instead of the O(n) string rebuild the unbounded ``+=`` incurred.
    """
    return append_capped_text(
        existing,
        delta,
        cap=MAX_AGGREGATED_OUTPUT,
        marker=OUTPUT_TRUNCATION_MARK,
    )


def _append_capped_stream_content(existing: str, delta: str) -> str:
    """Bound reasoning/message snapshots without dropping live deltas."""

    return append_capped_text(
        existing,
        delta,
        cap=MAX_STREAM_ITEM_CONTENT,
        marker=STREAM_CONTENT_TRUNCATION_MARK,
    )


_PUBLIC_NARRATIVE_UNSAFE_RE = re.compile(
    r"(<[^>]+>|[`$]|(?:^|\s)(?:[A-Za-z]:)?[/~][^\s]+|(?:token|secret|key|password)\s*[=:])",
    re.IGNORECASE,
)


def _safe_public_description(source: Any) -> str | None:
    """Return a short human-authored description if it is safe for main chat."""

    if not isinstance(source, dict):
        return None
    for key in (
        "public_description",
        "public_summary",
        "public_result",
        "description",
        "summary",
        "title",
    ):
        value = source.get(key)
        if not isinstance(value, str):
            continue
        text = " ".join(value.split())
        if not text or len(text) > 80:
            continue
        if _PUBLIC_NARRATIVE_UNSAFE_RE.search(text):
            continue
        return text
    return None


def _tool_start_public_narrative(evt: dict[str, Any]) -> str | None:
    return _safe_public_description(evt) or _safe_public_description(evt.get("input_preview"))


def _tool_done_public_narrative(
    evt: dict[str, Any],
) -> str | None:
    description = _safe_public_description(evt) or _safe_public_description(
        evt.get("output_preview")
    )
    if description:
        return description
    status = str(evt.get("status") or "success").casefold()
    if status in {"rejected", "declined"}:
        return "这一步需要授权，已暂停等待确认。"
    if status in {"cancelled", "interrupted"}:
        return "这一步已经停止，我会按当前状态收束。"
    if status == "error":
        return "这一步没有按预期完成，我会换个角度处理。"
    return None


# Diff-viewer tools: their ``diff`` output is a *view* of the current
# working tree — which may hold other sessions' uncommitted work — not a
# change this turn actually made. Promoting one into a FileChangeItem makes
# the verification gate attribute those files to this turn: a pure-read turn
# that ran ``git diff`` was hard-failed as if it had edited code
# (see ``tests/test_event_bridge_git_diff_promotion.py``). Only a write
# tool's own diff (``apply_patch``'s ``diff_preview``) represents a change
# the turn produced, so the diff-viewer family is excluded here.
_DIFF_VIEWER_TOOLS: frozenset[str] = frozenset({"git_diff", "git_show"})


def _file_change_item_from_tool_evt(evt: dict[str, Any]) -> FileChangeItem | None:
    """Build a structured FileChangeItem from a react_loop ``tool_end`` event.

    Two input shapes are accepted:

      * ``evt["diff"]``               — a raw unified-diff string (multi-file ok).
      * ``evt["file_changes"]``       — a pre-parsed list of
        ``{path, op, diff?, hunks?}`` dicts; skills that already know
        the shape can emit these directly to avoid a re-parse.

    Returns ``None`` when neither is present or both are empty — the
    caller must not emit an empty FileChangeItem. Returns ``None`` for a
    ``_DIFF_VIEWER_TOOLS`` tool regardless: a read-only diff viewer's output
    is inspection, never a change this turn made.
    """
    if evt.get("tool_name") in _DIFF_VIEWER_TOOLS:
        return None
    raw_diff = evt.get("diff")
    if isinstance(raw_diff, str) and raw_diff.strip():
        changes = parse_unified_diff(raw_diff)
        if changes:
            if diff_is_truncated(raw_diff):
                # The marker sits at the tail of the combined diff, so
                # only the last file's diff is known-incomplete.
                changes[-1].diff_truncated = True
            return FileChangeItem(changes=changes)

    raw_list = evt.get("file_changes")
    if isinstance(raw_list, list) and raw_list:
        parsed: list[FileChange] = []
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            op = entry.get("op")
            if not isinstance(path, str) or op not in ("create", "update", "delete"):
                continue
            diff_str = entry.get("diff") if isinstance(entry.get("diff"), str) else None
            hunks_raw = entry.get("hunks")
            hunks: list[FileHunk] = []
            if isinstance(hunks_raw, list):
                for h in hunks_raw:
                    if not isinstance(h, dict):
                        continue
                    hunks.append(
                        FileHunk(
                            old_start=int(h.get("old_start", h.get("oldStart", 0)) or 0),
                            old_lines=int(h.get("old_lines", h.get("oldLines", 0)) or 0),
                            new_start=int(h.get("new_start", h.get("newStart", 0)) or 0),
                            new_lines=int(h.get("new_lines", h.get("newLines", 0)) or 0),
                            body=str(h.get("body", "")),
                        )
                    )
            if not hunks and diff_str:
                sub = parse_unified_diff(diff_str)
                if sub:
                    hunks = sub[0].hunks
            parsed.append(
                FileChange(
                    path=path,
                    op=op,
                    diff=diff_str,
                    diff_truncated=diff_is_truncated(diff_str),
                    hunks=hunks,
                )
            )
        if parsed:
            return FileChangeItem(changes=parsed)
    return None


def _verification_item_from_tool_evt(
    command_item: CommandExecutionItem,
    evt: dict[str, Any],
    *,
    related_change_item_ids: list[str] | None = None,
    related_files: list[str] | None = None,
) -> VerificationItem | None:
    """Promote embedded post-write diagnostics to a first-class item."""

    explicit = evt.get("verification")
    if isinstance(explicit, dict):
        kind = explicit.get("kind")
        if kind not in {"test", "lint", "typecheck", "build", "diagnostic", "manual"}:
            kind = "manual"
        success = explicit.get("success")
        exit_code = explicit.get("exit_code")
        if not isinstance(exit_code, int):
            exit_code = 0 if success is True else None
        if success is True:
            status = ItemStatus.COMPLETED
        elif success is False:
            status = ItemStatus.FAILED
        elif isinstance(exit_code, int):
            status = ItemStatus.COMPLETED if exit_code == 0 else ItemStatus.FAILED
        else:
            status = ItemStatus.COMPLETED if evt.get("status") == "success" else ItemStatus.FAILED
        stdout_tail = explicit.get("stdout_tail")
        stderr_tail = explicit.get("stderr_tail")
        stdout_tail = stdout_tail[-4000:] if isinstance(stdout_tail, str) else None
        stderr_tail = stderr_tail[-4000:] if isinstance(stderr_tail, str) else None
        summary_src = stderr_tail or stdout_tail or command_item.aggregated_output
        summary = None
        if isinstance(summary_src, str) and summary_src.strip():
            summary = summary_src.strip().splitlines()[0][:240]
        command = explicit.get("command")
        if not isinstance(command, str) or not command.strip():
            command = command_item.command
        explicit_related_files = explicit.get("related_files")
        if not isinstance(explicit_related_files, list):
            explicit_related_files = explicit.get("relatedFiles")
        verification_related_files: list[str] = []
        if isinstance(explicit_related_files, list):
            verification_related_files = [
                path for path in explicit_related_files if isinstance(path, str) and path.strip()
            ]
        if not verification_related_files:
            verification_related_files = list(related_files or [])
        return VerificationItem(
            command=command,
            kind=kind,
            status=status,
            exit_code=exit_code,
            summary=summary,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            related_files=verification_related_files,
            related_change_item_ids=list(related_change_item_ids or []),
        )

    output = ""
    if isinstance(evt.get("output_preview"), str):
        output = evt["output_preview"]
    if command_item.aggregated_output:
        output = command_item.aggregated_output
    if not output:
        return None

    markers = (
        "[post-write diagnostics]",
        "[自动诊断结果]",
        "ruff diagnostics",
        "eslint diagnostics",
    )
    if not any(marker in output for marker in markers):
        return None

    diagnostic_related_files: list[str] = list(related_files or [])
    file_item = _file_change_item_from_tool_evt(evt)
    if file_item is not None:
        for change in file_item.changes:
            if change.path not in diagnostic_related_files:
                diagnostic_related_files.append(change.path)
    preview = command_item.input_preview
    if isinstance(preview, dict):
        path = preview.get("path") or preview.get("file_path")
        if isinstance(path, str) and path and path not in diagnostic_related_files:
            diagnostic_related_files.append(path)

    tail = output[-4000:]
    stripped = tail.strip()
    summary = stripped.splitlines()[0][:240] if stripped else None
    return VerificationItem(
        command="post-write diagnostics",
        kind="diagnostic",
        status=ItemStatus.FAILED,
        exit_code=1,
        summary=summary,
        stdout_tail=tail,
        stderr_tail=None,
        related_files=diagnostic_related_files,
        related_change_item_ids=list(related_change_item_ids or []),
    )
