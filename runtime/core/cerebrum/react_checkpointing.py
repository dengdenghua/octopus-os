"""Periodic auto-checkpoint + distributed mirror for the ReAct loop.

Moved from ``react_loop.py``: the iteration-interval knob
(``ECHO_CHECKPOINT_EVERY_N``), the Redis-shaped cross-machine
checkpoint mirror (``ECHO_CHECKPOINT_MIRROR_URL``), and the
message-rehydration bridge used when resuming from a checkpoint whose
``messages_snapshot`` predates its ``steps_snapshot``.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Generator
from typing import Any

from runtime.core.cerebrum.react_context import _serialize_messages_for_checkpoint
from runtime.core.cerebrum.react_types import ReActStep

_logger = logging.getLogger(__name__)

# ── Periodic auto-checkpoint (P3 — long-task durability) ──────────
# Existing checkpoints fire only on explicit pause or final-answer.
# When a process is hard-killed (SIGKILL, OOM, container restart) the
# turn loses everything between the last checkpoint and the kill.
# Periodic auto-checkpoint plugs that gap: every N iterations the
# loop writes the same shape of checkpoint that pause writes, so a
# resume request can pick up at the last completed iteration.
#
# Audit T-14: a full snapshot every iteration is O(n^2) for long turns —
# the default is now every 10 iterations (an order of magnitude less disk
# I/O); a hard kill loses at most one interval of progress. Override via
# ``ECHO_CHECKPOINT_EVERY_N`` env var (e.g. "1" for every iteration,
# "0" to disable). Errors during checkpoint write are swallowed; turn
# proceeds normally.

_DEFAULT_CHECKPOINT_INTERVAL = 10


def _checkpoint_interval() -> int:
    """How often (in iterations) to write an auto-checkpoint.

    Reads ``ECHO_CHECKPOINT_EVERY_N`` fresh on each call so an
    operator can flip the knob without a restart. On by default
    (every ``_DEFAULT_CHECKPOINT_INTERVAL`` iterations); an explicit
    ``"0"`` disables it. Missing, blank, negative, or unparseable
    values fall back to the default rather than silently disabling.
    """
    import os

    raw = os.environ.get("ECHO_CHECKPOINT_EVERY_N", "").strip()
    if not raw:
        return _DEFAULT_CHECKPOINT_INTERVAL
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_CHECKPOINT_INTERVAL
    if n < 0:
        return _DEFAULT_CHECKPOINT_INTERVAL
    return n  # n == 0 explicitly disables; n > 0 sets the interval


def _should_auto_checkpoint(iteration: int, interval: int) -> bool:
    """Whether iteration ``iteration`` should trigger an auto-checkpoint.

    Centralised so tests can drive it without spinning up the full
    react_loop. Returns False when ``interval <= 0`` (feature off) or
    when ``iteration <= 0`` (we never write a checkpoint at iteration
    0 — there's nothing to resume to). Otherwise fires when iteration
    is a non-zero multiple of ``interval``.
    """
    if interval <= 0 or iteration <= 0:
        return False
    return iteration % interval == 0


# ── Distributed checkpoint mirror (P3 cross-machine durability) ────
# Optional layer on top of the local journal: each auto-checkpoint
# also pushes a JSON snapshot to a shared KV store (Redis-shaped) so
# another machine can pick up the task. Off by default. Turn on via
# ``ECHO_CHECKPOINT_MIRROR_URL=redis://...`` env var.

_CHECKPOINT_MIRROR_SINGLETON: Any = None
_CHECKPOINT_MIRROR_INIT_DONE = False


def _checkpoint_mirror() -> Any:
    """Return the shared ``CheckpointMirror`` instance, or None.

    Disabled when ``ECHO_CHECKPOINT_MIRROR_URL`` is unset / empty.
    Build failures (redis package missing, bad URL) silently disable
    the mirror — the local journal is the source of truth, mirroring
    is a best-effort overlay.
    """
    global _CHECKPOINT_MIRROR_SINGLETON, _CHECKPOINT_MIRROR_INIT_DONE
    import os

    if not _CHECKPOINT_MIRROR_INIT_DONE:
        _CHECKPOINT_MIRROR_INIT_DONE = True
        url = os.environ.get("ECHO_CHECKPOINT_MIRROR_URL", "").strip()
        if not url:
            _CHECKPOINT_MIRROR_SINGLETON = None
        else:
            try:
                from runtime.core.cerebrum.checkpoint_mirror import (
                    build_checkpoint_mirror_from_url,
                )

                _CHECKPOINT_MIRROR_SINGLETON = build_checkpoint_mirror_from_url(url)
            except Exception as _exc:  # noqa: BLE001 — fail-soft
                _logger.debug("checkpoint mirror init failed: %s", _exc)
                _CHECKPOINT_MIRROR_SINGLETON = None
    return _CHECKPOINT_MIRROR_SINGLETON


def _reset_checkpoint_mirror_for_tests() -> None:
    """Reset the cached mirror singleton — used by tests for isolation."""
    global _CHECKPOINT_MIRROR_SINGLETON, _CHECKPOINT_MIRROR_INIT_DONE
    _CHECKPOINT_MIRROR_SINGLETON = None
    _CHECKPOINT_MIRROR_INIT_DONE = False


def _mirror_checkpoint(task_id: Any, checkpoint_dict: dict[str, Any]) -> None:
    """Best-effort write to the distributed mirror. Errors swallowed."""
    mirror = _checkpoint_mirror()
    if mirror is None:
        return
    with contextlib.suppress(Exception):
        mirror.put(str(task_id), checkpoint_dict)


# ── Per-step auto-checkpoint + evaluator (PHASE 6f) ───────────────
def _auto_checkpoint_and_evaluate_step(
    *,
    maybe_final: Any,
    step: ReActStep,
    stack: Any,
    react_task_id: Any,
    max_iterations: int,
    messages: list,
    steps: list[ReActStep],
    working_set: dict,
    progress_summary: Any,
    current_phase: Any,
    public_progress_summary: Any,
    step_evaluator: Any,
    retry_hint_sink: list[str],
) -> Generator[dict[str, Any], None, None]:
    """Auto-checkpoint after a completed step, then run the evaluator.

    Yields the ``evaluator_retry_hint`` event when a wired evaluator
    scores the step below threshold.  The retry hint is queued for Phase 6g so
    it lands *after* the assistant action and its Observation in model history.
    """
    # ── Periodic auto-checkpoint (P3 long-task durability) ──
    # Mirrors the pause path's checkpoint write so a SIGKILL or
    # OOM restart can resume from the last completed iteration.
    # On after every completed iteration by default; tune or disable via
    # ECHO_CHECKPOINT_EVERY_N=N (0 disables periodic snapshots).
    # Failures are swallowed; the turn must not break because
    # we couldn't snapshot.
    _ckpt_interval = _checkpoint_interval()
    if maybe_final is None and _should_auto_checkpoint(step.iteration, _ckpt_interval):
        _ckpt_journal_auto = getattr(stack, "journal", None)
        _auto_ckpt_payload = {
            "task_id": str(react_task_id) if react_task_id else "",
            "iteration_completed": step.iteration,
            "max_iterations": max_iterations,
            "messages_snapshot": _serialize_messages_for_checkpoint(messages),
            "steps_snapshot": [
                {
                    "iteration": s.iteration,
                    "thought": s.thought,
                    "public_update": s.public_update,
                    "action": s.action,
                    "actions": list(s.actions),
                    "observation": s.observation,
                    "action_results": [dict(result) for result in s.action_results],
                }
                for s in (steps + [step])
            ],
            "has_final_answer": False,
            "working_set_snapshot": list(working_set.values()),
            "progress_summary": progress_summary,
            "current_phase": current_phase,
        }
        if _ckpt_journal_auto is not None and hasattr(
            _ckpt_journal_auto,
            "write_react_checkpoint",
        ):
            with contextlib.suppress(Exception):
                _ckpt_journal_auto.write_react_checkpoint(
                    task_id=react_task_id,
                    iteration_completed=step.iteration,
                    max_iterations=max_iterations,
                    messages_snapshot=_auto_ckpt_payload["messages_snapshot"],
                    steps_snapshot=_auto_ckpt_payload["steps_snapshot"],
                    has_final_answer=False,
                    working_set_snapshot=_auto_ckpt_payload["working_set_snapshot"],
                    progress_summary=progress_summary,
                    current_phase=current_phase,
                )
        # Best-effort distributed mirror — off unless
        # ECHO_CHECKPOINT_MIRROR_URL is set. Same payload as the
        # journal write so downstream consumers see one shape.
        _mirror_checkpoint(react_task_id, _auto_ckpt_payload)

    # ── Step evaluator (optional) ────────────────────────
    # When wired, the evaluator scores the just-completed step.
    # A score below 0.3 triggers a retry hint injected into the
    # conversation so the LLM self-corrects on the next iteration.
    # This implements the "separate evaluator from generator"
    # pattern from Anthropic's harness-design research.
    if step_evaluator is not None:
        try:
            _eval_result = step_evaluator(
                {
                    "iteration": step.iteration,
                    "thought": step.thought,
                    "action": step.action,
                    "observation": step.observation,
                    "action_results": [dict(result) for result in step.action_results],
                    "progress_summary": public_progress_summary,
                }
            )
            _eval_score = getattr(_eval_result, "score", _eval_result)
            if isinstance(_eval_score, (int, float)) and _eval_score < 0.3:
                _retry_hint = str(getattr(_eval_result, "hint", "") or "").strip() or (
                    f"[evaluator] The previous step scored {_eval_score:.2f}/1.0 "
                    f"— quality is below threshold. Please reconsider your "
                    "approach and try a different strategy. Preserve completed "
                    "evidence. Do not blindly repeat a write, command, delete, "
                    "transaction, or other side-effecting action; inspect current "
                    "state first."
                )
                retry_hint_sink.append(_retry_hint)
                yield {
                    "type": "evaluator_retry_hint",
                    "iteration": step.iteration,
                    "score": _eval_score,
                    "hint": _retry_hint,
                    "category": str(getattr(_eval_result, "category", "") or ""),
                    "dedupe_key": str(getattr(_eval_result, "dedupe_key", "") or ""),
                }
        except Exception as _eval_exc:
            _logger.debug("step_evaluator raised: %s", _eval_exc)


def _rehydrate_messages_from_steps(messages: list, steps: list[ReActStep]) -> list:
    """Append missing step transcript when resuming from a checkpoint.

    Periodic checkpoints are written at a point where ``steps_snapshot``
    already includes the completed iteration, but ``messages_snapshot``
    may still be the pre-step conversation. Without this bridge a
    killed process can resume with the internal step list restored while
    the model cannot see the last Action/Observation in its prompt.
    """
    if not steps:
        return messages
    from runtime.platform.models.llm import Message

    existing = "\n".join(str(getattr(message, "content", "") or "") for message in messages)
    hydrated = list(messages)
    for step in steps:
        action = (step.action or "").strip()
        observation = (step.observation or "").strip()
        thought = (step.thought or "").strip()
        if not action and not observation:
            continue
        if action and action in existing and (not observation or observation in existing):
            continue
        assistant_lines: list[str] = []
        if thought:
            assistant_lines.append(f"Thought: {thought}")
        if action:
            assistant_lines.append(f"Action: {action}")
        if assistant_lines:
            assistant_content = "\n".join(assistant_lines)
            hydrated.append(Message(role="assistant", content=assistant_content))
            existing += "\n" + assistant_content
        if observation and observation not in existing:
            # TokenJuice on rehydration too — when resuming a
            # paused/checkpointed thread, prior tool observations
            # have to ride into the new prompt. Compressing them
            # saves tokens proportional to history depth.
            _obs_text = observation
            try:
                from runtime.core.cerebrum.token_juicer import (
                    is_enabled as _juice_enabled,
                )
                from runtime.core.cerebrum.token_juicer import (
                    juice as _juice,
                )

                if _juice_enabled():
                    _juiced, _stats = _juice(observation)
                    if _stats.passes:
                        _obs_text = _juiced
            except (ImportError, ValueError, TypeError):  # noqa: BLE001 — juice is best-effort, fall back to raw
                pass
            user_content = f"Observation: {_obs_text}\n\n继续下一轮推理。"
            hydrated.append(Message(role="user", content=user_content))
            existing += "\n" + user_content
    return hydrated
