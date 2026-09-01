"""In-flight nudges for the ReAct main loop (PHASE 6e, first half).

Moved from ``react_loop.py``: the soft guards that fire DURING the
loop, not at Final Answer time — background-task heartbeat,
completion / verification / code-semantic / green-verification
trackers, and the once-per-turn context-pressure signal. Each nudge
appends a short reminder to the current step's observation so the
model sees it before composing the next action. All are silent when
the model is already doing the right thing.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from runtime.core.cerebrum.react_execution import (
    _background_task_info_from_observation,
    _format_background_task_heartbeat,
)
from runtime.core.cerebrum.react_final_answer_guards import (
    _EXECUTION_DEGRADED_THRESHOLD,
    _environmental_failure_count,
    _step_is_environmental_failure,
)
from runtime.core.cerebrum.react_guards import (
    _code_semantic_followup_guard,
    _failed_verification_followup_guard,
    _has_successful_code_write,
    _redundant_green_verification_guard,
    _unverified_write_followup_guard,
)
from runtime.core.cerebrum.react_loop_controls import (
    _CONTEXT_PRESSURE_NUDGE,
    _estimate_context_fullness,
)
from runtime.core.cerebrum.react_types import ReActStep

_logger = logging.getLogger(__name__)


class _InFlightNudgeFlags(NamedTuple):
    """Post-nudge values of the four loop flags this block can flip."""

    context_pressure_signaled: bool
    green_verification_convergence_active: bool
    force_convergence_next: bool
    env_degradation_signaled: bool
    terminal_convergence_active: bool


# Fires ONCE per turn, right after the first environmental tool failure
# (sandbox / network / OS-permission denial). Tells the model to stop
# retrying the blocked tool and pivot: verify statically, close the turn,
# and state the execution restriction plainly in the Final Answer. This is
# the model's one heads-up that run-evidence guards are auto-downgraded at
# Final Answer time — so it should never fabricate a green that "would have"
# run (that is still caught by the hard false-verification guard).
_ENV_DEGRADATION_NUDGE = (
    "[environment-degraded]\n"
    "Tool execution is being blocked by the environment (sandbox / network / "
    "OS permissions) — retrying the same tool will keep failing. Pivot to "
    "static evidence: read_file your written files back to confirm they "
    "landed, inspect the diff, and finish. In the Final Answer state plainly "
    "that dynamic verification (tests / typecheck / build) could not run "
    "because the execution environment is restricted, and list what you "
    "verified statically instead. Never claim a test or check passed unless "
    "it actually ran — a fabricated green is detected and rejected."
)


def _trajectory_has_successful_code_write(steps: list[ReActStep]) -> bool:
    """Require success for the write action itself in mixed-action batches."""

    for step in steps:
        actions = list(step.actions or ([step.action] if step.action else []))
        if step.action_results:
            for index, result in enumerate(step.action_results):
                if result.get("ok") is not True or index >= len(actions):
                    continue
                action_step = ReActStep(
                    iteration=step.iteration,
                    action=actions[index],
                    actions=[actions[index]],
                    observation=str(result.get("observation") or ""),
                    action_results=[result],
                )
                if _has_successful_code_write([action_step]):
                    return True
            continue
        if _has_successful_code_write([step]):
            return True
    return False


def _should_terminal_environment_convergence(
    steps: list[ReActStep],
    *,
    is_code_mode: bool,
) -> bool:
    """Recompute sticky convergence from durable, trusted action receipts."""

    return bool(
        is_code_mode
        and _trajectory_has_successful_code_write(steps)
        and _environmental_failure_count(steps) >= _EXECUTION_DEGRADED_THRESHOLD
    )


def _apply_in_flight_nudges(
    *,
    steps: list,
    step: ReActStep,
    i: int,
    known_background_tasks: dict,
    todo_protocol_required: bool,
    todo_protocol_visible: bool,
    is_code_mode: bool,
    messages: list,
    effective_model: str,
    context_pressure_signaled: bool,
    green_verification_convergence_active: bool,
    force_convergence_next: bool,
    env_degradation_signaled: bool,
    terminal_convergence_active: bool = False,
) -> _InFlightNudgeFlags:
    """Append any due in-flight nudges to ``step.observation``.

    Mutates ``step.observation`` and ``known_background_tasks`` in
    place; returns the post-state of the three loop flags for the
    caller to unpack.
    """
    # ── In-flight nudges (echo optimisation §15 + §18) ───
    # Two soft guards that fire DURING the loop, not at Final
    # Answer time. They append a short reminder to this step's
    # observation so the model sees it before composing the
    # next action. Both are silent when the model is already
    # doing the right thing.
    _steps_with_current = steps + [step]
    _midflight_nudges: list[str] = []
    # Track any background process snapshot present in this
    # step's observation so the periodic heartbeat below can
    # remind the model about live processes.
    _bg_task_info = _background_task_info_from_observation(step.observation)
    if _bg_task_info is not None:
        _bg_task_id = _bg_task_info.get("task_id")
        if isinstance(_bg_task_id, str) and _bg_task_id:
            known_background_tasks[_bg_task_id] = _bg_task_info
    # Heartbeat: every 5 iterations (i > 0 and i % 5 == 0),
    # if we have any registered background tasks, append a
    # reminder to the NEXT step's observation injection.
    if i > 0 and i % 5 == 0 and known_background_tasks:
        _midflight_nudges.append(
            _format_background_task_heartbeat(list(known_background_tasks.keys()))
        )
    # Do not infer task state from prose such as "done" / "修好了". The
    # checklist and tool receipts are the state machine; natural-language
    # commentary is presentation only. Phrase-based nudging caused equivalent
    # work to behave differently when providers or users changed wording.
    _verify_nudge = _unverified_write_followup_guard(
        _steps_with_current,
        is_code_mode=is_code_mode,
    )
    if _verify_nudge:
        _midflight_nudges.append(f"[verification-tracker]\n{_verify_nudge}")
    _red_verify_nudge = _failed_verification_followup_guard(
        _steps_with_current,
        is_code_mode=is_code_mode,
    )
    if _red_verify_nudge:
        _midflight_nudges.append(f"[red-verification-recovery]\n{_red_verify_nudge}")
    _concurrency_nudge = _code_semantic_followup_guard(
        _steps_with_current,
        is_code_mode=is_code_mode,
    )
    if _concurrency_nudge:
        _midflight_nudges.append(f"[code-semantic-repair]\n{_concurrency_nudge}")
    _green_verify_nudge = _redundant_green_verification_guard(
        _steps_with_current,
        is_code_mode=is_code_mode,
    )
    if _green_verify_nudge:
        _midflight_nudges.append(f"[green-verification-convergence]\n{_green_verify_nudge}")
        green_verification_convergence_active = True
        force_convergence_next = True
    # Context-pressure signal — fires once per turn when the rolling
    # message list approaches the model's context budget. Gives the
    # model a chance to write a "resume state" hand-off paragraph
    # before _compress_context starts dropping older steps.
    if not context_pressure_signaled:
        _ctx_ratio = _estimate_context_fullness(messages, effective_model)
        if _ctx_ratio > 0.70:
            _midflight_nudges.append(_CONTEXT_PRESSURE_NUDGE.format(level=f"{_ctx_ratio:.0%}"))
            context_pressure_signaled = True
    # Environment-degradation guidance — once per turn, right after the
    # first environmental tool failure (see _ENV_DEGRADATION_NUDGE).
    if not env_degradation_signaled and any(
        _step_is_environmental_failure(s) for s in _steps_with_current
    ):
        _midflight_nudges.append(_ENV_DEGRADATION_NUDGE)
        env_degradation_signaled = True
    if (
        _should_terminal_environment_convergence(
            _steps_with_current,
            is_code_mode=is_code_mode,
        )
        and not terminal_convergence_active
    ):
        _midflight_nudges.append(
            "[environment-verification-convergence]\n"
            "The runtime recorded repeated trusted verifier environment gaps after a "
            "successful code write. The next round is terminal synthesis with tools "
            "disabled. Preserve the failed verifier facts and state that dynamic "
            "verification could not run. Never claim tests, lint, typecheck, or build passed."
        )
        force_convergence_next = True
        terminal_convergence_active = True
    if _midflight_nudges:
        step.observation = (
            ((step.observation or "") + "\n\n") if step.observation else ""
        ) + "\n\n".join(_midflight_nudges)
    return _InFlightNudgeFlags(
        context_pressure_signaled=context_pressure_signaled,
        green_verification_convergence_active=green_verification_convergence_active,
        force_convergence_next=force_convergence_next,
        env_degradation_signaled=env_degradation_signaled,
        terminal_convergence_active=terminal_convergence_active,
    )
