"""Action outcome bookkeeping for the ReAct loop.

Extracted from ``react_loop.py`` (Wave 1 of the split documented in
``docs/design/react-loop-split-plan.md``). Pure helpers that decide whether a
tool call succeeded, split a multi-tool round into per-action outcomes, and
fingerprint/deduplicate requested actions for retry control.
"""

from __future__ import annotations

import json
import re

from runtime.core.cerebrum.react_execution import _beak_step_effective_success
from runtime.core.cerebrum.react_parsing import _parse_action
from runtime.core.cerebrum.react_types import ReActStep
from runtime.execution.tool_engine.effect_receipts import is_side_effecting
from runtime.platform.models import Step


def _tool_call_succeeded(observation: str | None, beak_step: Step | None) -> bool:
    """Whether a single tool call succeeded. A beak step's effective-success
    verdict wins when present; otherwise sniff the failure-prefixed observation
    text. PHASE 6d uses this for both the initial call and its auto-retry."""
    if beak_step is not None:
        return _beak_step_effective_success(beak_step)
    return not (
        observation is not None and observation.startswith(("(工具失败)", "(工具执行异常)"))
    )


def _per_action_outcomes(
    step: ReActStep,
    *,
    default_ok: bool,
) -> list[tuple[ReActStep, bool]]:
    """Split a multi-tool model round into ordered evidence outcomes."""
    actions = step.actions or ([step.action] if step.action else [])
    if not actions:
        return []
    if len(step.action_results) == len(actions):
        outcomes: list[tuple[ReActStep, bool]] = []
        for action, result in zip(actions, step.action_results, strict=True):
            outcomes.append(
                (
                    ReActStep(
                        iteration=step.iteration,
                        action=action,
                        observation=str(result.get("observation") or ""),
                    ),
                    result.get("ok") is True,
                )
            )
        return outcomes
    if len(actions) == 1:
        return [
            (
                ReActStep(
                    iteration=step.iteration,
                    action=actions[0],
                    observation=step.observation,
                ),
                default_ok,
            )
        ]
    # Legacy providers occasionally return a merged observation without
    # per-action receipts. Preserve the old one-round semantics rather than
    # inventing success for individual calls we cannot attribute.
    return [(step, default_ok)]


def _action_fingerprint(action: str) -> str:
    """Return a stable tool+arguments key for duplicate/retry control."""
    parsed = _parse_action(action)
    if parsed is None:
        return " ".join(str(action or "").split())
    name, args = parsed
    try:
        payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(args)
    return f"{name}:{payload}"


def _deduplicate_actions(actions: list[str]) -> tuple[list[str], int]:
    """Collapse protocol/provider duplicate calls within one model round."""
    unique: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for action in actions:
        fingerprint = _action_fingerprint(action)
        if fingerprint in seen:
            duplicates += 1
            continue
        seen.add(fingerprint)
        unique.append(action)
    return unique, duplicates


def _action_batch_fingerprint(actions: list[str]) -> str:
    """Stable ordered fingerprint for one or many requested tool calls."""
    fingerprints = [_action_fingerprint(action) for action in actions]
    if len(fingerprints) == 1:
        return fingerprints[0]
    return "batch:" + json.dumps(fingerprints, ensure_ascii=False, separators=(",", ":"))


# Markers that indicate a tool returned ok=True but produced no real
# effect — an empty list, zero count, or an empty payload.  Used by the
# silent no-op detector in react_execution to catch "wrong key" loops
# where the handler swallows unknown arguments and returns a valid-but-
# empty result.
_NOOP_OBSERVATION_RE = re.compile(
    r'"count"\s*:\s*0'  # {"count": 0, ...}
    r'|\\?"count\\?"\s*:\s*0'  # escaped form in a serialized string
    r'|\btodos\\?"\s*:\s*\[\s*\]'  # "todos": []
    r'|\bresults\\?"\s*:\s*\[\s*\]'  # "results": []
    r"|No files found"
    r"|无匹配"
    r"|未找到",
    re.IGNORECASE,
)


def _observation_is_noop(observation: str) -> bool:
    """Whether a successful tool call produced no real effect.

    Intentionally narrow: only matches obvious empty-result markers in
    the serialized observation.  Read tools that legitimately return
    empty (e.g. ``list_cwd`` on an empty dir) are not penalised because
    the detector requires the SAME fingerprint to repeat — a genuine
    empty-dir listing won't be repeated with identical args.
    """
    if not observation:
        return False
    return bool(_NOOP_OBSERVATION_RE.search(observation))


def _retry_safe_affinity(affinity: list[str] | None) -> bool:
    """Whether a failed tool may be auto-retried once.

    Reuse the effect-receipt layer's fail-closed classifier instead of keeping
    a looser ReAct-only denylist.  A tool is retry-safe only when it carries an
    explicit read-only affinity.  Empty, unknown, domain-only (for example
    ``trade``/``order``), or explicitly mutating affinities are unsafe because
    the first attempt may already have changed external state.
    """

    return not is_side_effecting(affinity)
