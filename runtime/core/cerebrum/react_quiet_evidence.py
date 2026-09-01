"""Quiet-evidence accumulation for the ReAct loop's public narrative.

Extracted from ``react_loop.py`` (Wave 1 of the split documented in
``docs/design/react-loop-split-plan.md``). A single successful read is
execution detail, not a user-facing beat; these helpers decide when
accumulated silent reads merit one model-authored public checkpoint.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_parallel_dispatch import _WRITE_TOOLS
from runtime.core.cerebrum.react_parsing import _parse_action
from runtime.core.cerebrum.react_public_updates import _public_tool_target
from runtime.core.cerebrum.react_types import ReActStep


def _result_checkpoint_is_meaningful(
    actions: list[str],
    *,
    succeeded: bool,
) -> bool:
    """Keep milestones and recovery visible while suppressing read-by-read noise."""
    if not succeeded or len(actions) > 1:
        return True
    parsed = [entry for action in actions if (entry := _parse_action(action))]
    if not parsed:
        return False
    name = parsed[0][0].lower()
    return (
        name in _WRITE_TOOLS
        or any(token in name for token in ("write", "edit", "patch", "replace"))
        or name in {"exec_shell", "shell", "run_command"}
        or "search" in name
        or name in {"fetch_url", "browser_open", "browser_get_content"}
    )


def _quiet_evidence_targets(steps: list[ReActStep]) -> set[str]:
    """Collect distinct read-only evidence targets that have stayed silent.

    A single successful file read is useful execution detail but usually not
    worth another public sentence. Two different inspected targets establish
    enough comparative evidence for the model to say what is now known. The
    decision is structural: it never classifies prose or invents a phase name.
    """

    targets: set[str] = set()
    quiet_tools = {
        "read_file",
        "read_text_file",
        "list_cwd",
        "glob",
        "grep",
        "view_file",
    }
    for step in steps:
        actions = step.actions or ([step.action] if step.action else [])
        for action in actions:
            parsed = _parse_action(action)
            if parsed is None:
                continue
            name, args = parsed
            if name.lower() not in quiet_tools:
                continue
            target = ""
            if isinstance(args, dict):
                target = next(
                    (
                        str(args[key]).strip()
                        for key in ("path", "file_path", "filepath", "filename")
                        if isinstance(args.get(key), str) and str(args[key]).strip()
                    ),
                    "",
                )
                if not target:
                    target = _public_tool_target(args)
            if target:
                targets.add(target.casefold())
    return targets


def _quiet_evidence_checkpoint_due(steps: list[ReActStep]) -> bool:
    """Whether accumulated quiet reads merit one model-authored public beat."""

    return len(_quiet_evidence_targets(steps)) >= 2


def _should_accumulate_quiet_evidence(
    step: ReActStep,
    *,
    succeeded: bool,
    observation: str,
) -> bool:
    """Keep successful read evidence even when it arrived in one parallel batch."""

    return succeeded and bool(observation) and bool(_quiet_evidence_targets([step]))
