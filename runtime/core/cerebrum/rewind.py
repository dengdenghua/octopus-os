"""Turn-scoped rewind · roll a task back to a prior checkpoint anchor.

Existing pieces
---------------
* ``ReactCheckpointEvent`` (``runtime/memory/journal/journal.py``) is
  written after every completed ReAct iteration and carries
  ``iteration_completed`` + ``working_set_snapshot``.
* ``FileOpEvent`` records every file mutation with a ``rollback`` payload
  (path / action / expected_current_sha256 / content / before_sha256).
* ``apply_file_rollback_ledger`` (``runtime/memory/runtime_state/
  file_transactions.py``) applies such a ledger with optimistic hash
  checks.

What was missing
----------------
The existing REST endpoints (``/api/files/rollback/preview`` /
  ``/apply``) only filter by ``event_id`` / ``task_id`` / ``path`` —
they roll back **all** matching file ops. There is no way to say
"roll this task back to the state it was in at iteration N" — which
is exactly the ``/rewind`` ergonomics Grok Build popularised.

This module fills that gap:

* :func:`list_rewind_points` — enumerate checkpoint anchors for a task.
* :func:`rewind_to_checkpoint` — apply only the file ops that landed
  *after* the chosen checkpoint, in reverse order.

It reuses ``apply_file_rollback_ledger`` verbatim — we just slice the
event stream differently. No new on-disk format, no new journal event
type beyond the existing ``FileRollbackEvent``.

Safety
------
* ``dry_run=True`` returns the preview without touching disk.
* Optimistic hash checks (already in ``apply_file_rollback_ledger``)
  refuse to overwrite a file whose current content doesn't match the
  recorded ``expected_current_sha256`` — so concurrent edits can't be
  silently clobbered.
* Non-file side effects (shell commands, network calls) are NOT
  reversible; the returned :class:`RewindResult` makes that explicit
  via ``non_reversible_warnings``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.memory.runtime_state.file_transactions import (
    FileRollbackResult,
    apply_file_rollback_ledger,
)


@dataclass(frozen=True)
class RewindPoint:
    """A single rewind anchor — one ``react_checkpoint`` event."""

    event_id: str
    task_id: str
    iteration: int
    ts: str
    phase: str
    has_final_answer: bool
    working_set_paths: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "iteration": self.iteration,
            "ts": self.ts,
            "phase": self.phase,
            "has_final_answer": self.has_final_answer,
            "working_set_paths": list(self.working_set_paths),
        }


@dataclass(frozen=True)
class RewindResult:
    """Outcome of a rewind operation."""

    target: RewindPoint
    file_rollback: FileRollbackResult
    # Warnings about non-reversible side effects observed between
    # the target checkpoint and the latest state. Callers SHOULD
    # surface these to the user before they assume the rewind is
    # total. Examples: shell commands, network POSTs, MCP calls.
    non_reversible_warnings: tuple[str, ...] = field(default_factory=tuple)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.to_dict(),
            "file_rollback": self.file_rollback.to_dict(),
            "non_reversible_warnings": list(self.non_reversible_warnings),
            "dry_run": self.dry_run,
        }


# ── Public API ───────────────────────────────────────────────


def list_rewind_points(journal: Any, task_id: str) -> list[RewindPoint]:
    """Enumerate every checkpoint anchor for ``task_id``, oldest-first.

    Reads ``react_checkpoint`` events from the journal and maps each
    to a :class:`RewindPoint`. The latest checkpoint is the natural
    "current state" anchor; earlier ones are valid rewind targets.
    """
    out: list[RewindPoint] = []
    for event in journal.read_by_type("react_checkpoint"):
        if str(getattr(event, "task_id", "") or "") != task_id:
            continue
        out.append(_event_to_rewind_point(event))
    return out


def rewind_to_checkpoint(
    journal: Any,
    task_id: str,
    target_iteration: int,
    *,
    project_root: str | None = None,
    dry_run: bool = False,
) -> RewindResult:
    """Roll a task back to the state captured at ``target_iteration``.

    Strategy: gather every ``file_op`` event whose ``ts`` is strictly
    after the target checkpoint's ``ts`` (and belongs to ``task_id``),
    feed that slice to ``apply_file_rollback_ledger`` which already
    applies entries in reverse order with optimistic hash checks.

    Parameters
    ----------
    journal
        Any object with ``read_by_type(event_type: str) -> Iterable``
        returning event objects with ``task_id`` / ``ts`` / ``event_id``
        attributes (the existing ``Journal`` fits).
    task_id
        The task to rewind.
    target_iteration
        The ``iteration_completed`` value on the target checkpoint.
        If multiple checkpoints share that iteration, the earliest
        is used (so we rewind the most).
    project_root
        Filesystem root for resolving relative paths in the rollback
        ledger. Forwarded to ``apply_file_rollback_ledger``.
    dry_run
        If True, returns the preview without writing to disk.

    Raises
    ------
    ValueError
        If no checkpoint matches ``target_iteration`` for the task.
    """
    points = list_rewind_points(journal, task_id)
    target = next(
        (p for p in points if p.iteration == target_iteration),
        None,
    )
    if target is None:
        raise ValueError(
            f"no react_checkpoint found for task {task_id!r} at iteration {target_iteration}"
        )

    # Slice file_op events that landed AFTER the target checkpoint.
    file_ops_after = _file_ops_after(journal, task_id, target.ts)
    file_result = apply_file_rollback_ledger(
        file_ops_after,
        project_root=project_root,
        dry_run=dry_run,
    )

    warnings = _collect_non_reversible_warnings(journal, task_id, target.ts)

    return RewindResult(
        target=target,
        file_rollback=file_result,
        non_reversible_warnings=warnings,
        dry_run=dry_run,
    )


def latest_rewind_point(journal: Any, task_id: str) -> RewindPoint | None:
    """Convenience: the most recent checkpoint for a task, or None."""
    points = list_rewind_points(journal, task_id)
    return points[-1] if points else None


# ── Helpers ──────────────────────────────────────────────────


def _event_to_rewind_point(event: Any) -> RewindPoint:
    working_set = getattr(event, "working_set_snapshot", []) or []
    # ``working_set_snapshot`` is a list of dicts; extract ``path``
    # defensively — older journals may store bare strings.
    paths: list[str] = []
    for entry in working_set:
        if isinstance(entry, str):
            paths.append(entry)
        elif isinstance(entry, dict):
            path = entry.get("path") or entry.get("name") or ""
            if path:
                paths.append(str(path))
    return RewindPoint(
        event_id=str(getattr(event, "event_id", "") or ""),
        task_id=str(getattr(event, "task_id", "") or ""),
        iteration=int(getattr(event, "iteration_completed", 0) or 0),
        ts=str(getattr(event, "ts", "") or ""),
        phase=str(getattr(event, "current_phase", "") or ""),
        has_final_answer=bool(getattr(event, "has_final_answer", False)),
        working_set_paths=tuple(paths),
    )


def _file_ops_after(
    journal: Any,
    task_id: str,
    target_ts: str,
) -> list[Any]:
    """Return ``file_op`` events for ``task_id`` with ``ts > target_ts``."""
    out: list[Any] = []
    for event in journal.read_by_type("file_op"):
        if str(getattr(event, "task_id", "") or "") != task_id:
            continue
        if str(getattr(event, "ts", "") or "") <= target_ts:
            continue
        out.append(event)
    return out


def _collect_non_reversible_warnings(
    journal: Any,
    task_id: str,
    target_ts: str,
) -> tuple[str, ...]:
    """Surface non-file side effects that the rewind can't undo.

    Looks for ``step`` events whose ``sucker_id`` names a known
    non-reversible skill (shell/deploy/push/etc.) OR whose action
    text contains destructive keywords. Conservative — false
    positives beat silently letting a user believe a ``rm -rf``
    got undone.

    Handles two event shapes:
      * ``StepEvent`` (production): ``event.step.action.sucker_id``
        + ``event.step.action.args`` (dict, may contain a ``command``
        string for shell skills).
      * flat events (tests / legacy): ``event.sucker_id`` +
        ``event.action`` as a plain string.
    """
    warnings: list[str] = []
    irreversible_sucker_prefixes = (
        "exec_shell",
        "shell_",
        "git_push",
        "deploy_",
        "send_",
        "post_",
        "webhook_",
        "mcp_call_",
    )
    destructive_keywords = ("rm -rf", "git push", "deploy ", "curl -x", "curl -d")

    for event in journal.read_by_type("step"):
        if str(getattr(event, "task_id", "") or "") != task_id:
            continue
        if str(getattr(event, "ts", "") or "") <= target_ts:
            continue

        # Resolve sucker_id from either shape.
        sucker_id = str(getattr(event, "sucker_id", "") or "")
        action_obj = getattr(event, "action", None)
        action_text = ""
        if isinstance(action_obj, str):
            action_text = action_obj
        elif action_obj is not None:
            # Production StepEvent: event.step.action is a ToolCall.
            sucker_id = sucker_id or str(getattr(action_obj, "sucker_id", "") or "")
            args = getattr(action_obj, "args", None)
            if isinstance(args, dict):
                # Common shell skills put the command under
                # ``command`` / ``cmd`` / ``script``.
                for key in ("command", "cmd", "script"):
                    val = args.get(key)
                    if isinstance(val, str):
                        action_text = val
                        break
        # Also try nested event.step.action (real StepEvent).
        step_obj = getattr(event, "step", None)
        if step_obj is not None and not sucker_id:
            nested_action = getattr(step_obj, "action", None)
            if nested_action is not None:
                sucker_id = str(getattr(nested_action, "sucker_id", "") or "")
                nested_args = getattr(nested_action, "args", None)
                if isinstance(nested_args, dict) and not action_text:
                    for key in ("command", "cmd", "script"):
                        val = nested_args.get(key)
                        if isinstance(val, str):
                            action_text = val
                            break

        if not sucker_id and not action_text:
            continue

        if sucker_id and sucker_id.startswith(irreversible_sucker_prefixes):
            warnings.append(f"non-reversible skill '{sucker_id}' at {getattr(event, 'ts', '')}")
            continue

        action_lower = action_text.lower()
        if any(kw in action_lower for kw in destructive_keywords):
            warnings.append(
                f"potentially non-reversible action at {getattr(event, 'ts', '')}: "
                f"{action_text[:120]}"
            )
    return tuple(warnings)


__all__ = [
    "RewindPoint",
    "RewindResult",
    "list_rewind_points",
    "latest_rewind_point",
    "rewind_to_checkpoint",
]
