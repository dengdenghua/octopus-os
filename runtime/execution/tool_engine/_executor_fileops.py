from __future__ import annotations

import hashlib
from typing import Any

from runtime.execution.suckers import Skill
from runtime.memory.journal import Journal
from runtime.platform.models import ArmId, TaskId

# ───────────────────────────────────────────────────────────────
# FileOp extraction
# ───────────────────────────────────────────────────────────────


_ACTION_PRIORITY: list[tuple[str, str]] = [
    ("delete", "delete"),
    ("rename", "rename"),
    ("create", "create"),
    ("edit", "edit"),
    ("write", "write"),
]


def _infer_action(affinity: list[str], sucker_id: str) -> str:
    names = set(affinity or [])
    for tag, action in _ACTION_PRIORITY:
        if tag in names:
            return action
    # Fall back to sucker_id substring match
    low = sucker_id.lower()
    for tag, action in _ACTION_PRIORITY:
        if tag in low:
            return action
    return "write"


def _extract_path(args: dict[str, Any], output: Any) -> str:
    for key in ("path", "filepath", "file_path", "file", "target", "dest", "dest_path"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return val
    if isinstance(output, dict):
        for key in ("path", "filepath", "file", "written"):
            val = output.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


_FILE_DIFF_PRE_READ_LIMIT = 100_000  # Implementation note.
_FILE_DIFF_OUTPUT_LIMIT = 20_000  # Implementation note.
_FILE_DIFF_CONTEXT_LINES = 3  # Implementation note.
_FILE_ROLLBACK_CONTENT_LIMIT = 100_000


def _try_read_pre_content(path: str) -> str | None:
    if not path:
        return None
    import os

    try:
        if not os.path.isfile(path):
            return None
        size = os.path.getsize(path)
        if size > _FILE_DIFF_PRE_READ_LIMIT:
            return None
        with open(path, "rb") as fh:
            blob = fh.read(_FILE_DIFF_PRE_READ_LIMIT)
        return blob.decode("utf-8", errors="strict").replace("\r\n", "\n")
    except (OSError, ValueError):  # Implementation note.
        return None


def _compute_unified_diff(
    old: str,
    new: str,
    path: str,
) -> str | None:
    old_n = old.replace("\r\n", "\n")
    new_n = new.replace("\r\n", "\n")
    if old_n == new_n:
        return None
    old, new = old_n, new_n
    import difflib

    lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=_FILE_DIFF_CONTEXT_LINES,
        )
    )
    if not lines:
        return None
    diff = "".join(lines)
    if len(diff) > _FILE_DIFF_OUTPUT_LIMIT:
        head = diff[:_FILE_DIFF_OUTPUT_LIMIT]
        omitted = len(diff) - len(head)
        # Marker format is a wire contract: runtime/protocol/items.py
        # ``diff_is_truncated`` matches it to set FileChange.diff_truncated
        # downstream. Change both together.
        return head + f"\n... (truncated {omitted} bytes)\n"
    return diff


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rollback_payload(
    *,
    path: str,
    action: str,
    pre_exists: bool,
    pre_content: str | None,
    new_content: str | None,
) -> dict[str, Any] | None:
    if action == "rename":
        return {
            "reversible": False,
            "reason": "rename_rollback_not_supported",
            "path": path,
        }
    if pre_exists and pre_content is None:
        return {
            "reversible": False,
            "reason": "previous_content_unavailable",
            "path": path,
        }
    if new_content is not None and len(new_content.encode("utf-8")) > _FILE_ROLLBACK_CONTENT_LIMIT:
        return {
            "reversible": False,
            "reason": "new_content_too_large",
            "path": path,
        }
    if pre_content is not None and len(pre_content.encode("utf-8")) > _FILE_ROLLBACK_CONTENT_LIMIT:
        return {
            "reversible": False,
            "reason": "previous_content_too_large",
            "path": path,
        }
    if action == "delete":
        if pre_content is None:
            return {
                "reversible": False,
                "reason": "deleted_content_unavailable",
                "path": path,
            }
        return {
            "reversible": True,
            "action": "write",
            "path": path,
            "content": pre_content,
            "expected_current_sha256": "",
            "before_sha256": _sha256_text(pre_content),
            "after_sha256": "",
        }
    if not pre_exists:
        if new_content is None:
            return {
                "reversible": False,
                "reason": "new_content_unavailable",
                "path": path,
            }
        return {
            "reversible": True,
            "action": "delete",
            "path": path,
            "expected_current_sha256": _sha256_text(new_content),
            "before_sha256": "",
            "after_sha256": _sha256_text(new_content),
        }
    if pre_content is None or new_content is None:
        return {
            "reversible": False,
            "reason": "content_unavailable",
            "path": path,
        }
    return {
        "reversible": True,
        "action": "write",
        "path": path,
        "content": pre_content,
        "expected_current_sha256": _sha256_text(new_content),
        "before_sha256": _sha256_text(pre_content),
        "after_sha256": _sha256_text(new_content),
    }


def _emit_file_op_from_step(
    *,
    journal: Journal,
    skill: Skill,
    args: dict[str, Any],
    output: Any,
    task_id: TaskId,
    arm_id: ArmId,
    actor: str | None,
    pre_content: str | None = None,
    pre_exists: bool = False,
) -> None:
    path = _extract_path(args, output)
    if not path:
        return  # Implementation note.
    action = _infer_action(list(skill.affinity), skill.name)

    new_size: int | None = None
    new_content: str | None = None
    if isinstance(output, dict):
        for key in ("bytes_written", "size", "new_size"):
            v = output.get(key)
            if isinstance(v, int):
                new_size = v
                break
        for key in ("content", "new_content", "written_content"):
            v = output.get(key)
            if isinstance(v, str):
                new_content = v
                break
    if new_content is None:
        raw = args.get("content") or args.get("text")
        if isinstance(raw, str):
            new_content = raw
    if new_size is None and new_content is not None:
        new_size = len(new_content.encode("utf-8"))

    diff: str | None = None
    readback_path = path
    if isinstance(output, dict):
        for key in ("path", "filepath", "file", "written"):
            val = output.get(key)
            if isinstance(val, str) and val:
                readback_path = val
                break
    if new_content is None and action != "delete":
        new_content = _try_read_pre_content(readback_path)
        if new_size is None and new_content is not None:
            new_size = len(new_content.encode("utf-8"))
    if new_content is not None and action != "delete":
        diff = _compute_unified_diff(
            pre_content or "",
            new_content,
            path,
        )
    elif action == "delete" and pre_content is not None:
        diff = _compute_unified_diff(pre_content, "", path)

    # Inline diff preview for chat tool-result bubbles. The frontend
    # message bubble already knows how to render ``result.diff_preview``
    # for write/edit tool calls; we just never populated it. Best-effort
    # only · never let preview generation break the main tool result.
    if diff and isinstance(output, dict) and "diff_preview" not in output:
        output["diff_preview"] = diff[:4000]

    old_size: int | None = None
    if pre_content is not None:
        old_size = len(pre_content.encode("utf-8"))
    rollback = _rollback_payload(
        path=path,
        action=action,
        pre_exists=pre_exists,
        pre_content=pre_content,
        new_content=new_content,
    )

    if not hasattr(journal, "write_file_op"):
        return
    journal.write_file_op(
        path=path,
        action=action,
        sucker_id=str(skill.name),
        task_id=task_id,
        arm_id=arm_id,
        actor=actor,
        old_size=old_size,
        new_size=new_size,
        diff=diff,
        rollback=rollback,
    )
