"""Shared per-turn state for the ReAct main-loop phases (Wave 2).

``_LoopState`` is the minimal skeleton covering exactly what PHASE 6c
reads or writes today — no speculative fields for 6b/6d/6e yet; they
join as those phases are extracted. Reference-typed fields (``steps``,
``executed_beak_steps``, ``guard_impasse_state``) are shared with the
main loop and mutated in place; scalar fields are synced local→state
before a phase call and state→local after it, so the loop body stays
the single source of truth between phase extractions.

Depends only on react_types / platform-level types; must never import
react_loop.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from runtime.core.cerebrum.react_types import ReActStep


class _LoopControl(enum.Enum):
    """Control signal returned by extracted phase generators."""

    CONTINUE = "continue"  # proceed to the next phase / iteration
    NEXT_ITERATION = "next_iteration"  # skip remaining phases; next loop iteration
    BREAK = "break"  # exit the iteration loop; state carries terminated_reason/final_answer
    RETURN_NONE = "return_none"  # abort the turn; persist/unregister already done


@dataclass
class _LoopState:
    """Minimal per-turn state shared between stream_react_loop and phases."""

    # ── cfg · turn-level wiring (assembled once, read-only in 6c) ──
    stack: Any = None
    goal: str = ""
    executor: Any = None
    react_task_id: Any = None
    pause_controller: Any = None
    effective_wp: Any = None
    format_violation_bail_at: int = 2
    final_guard_grounded_source_paths: Any = None
    guard_impasse_state: dict = field(default_factory=dict)
    # Tool observations from EARLIER turns of this thread (``Observation:``
    # user messages in the assembled history). Research guards merge this into
    # their evidence stream so cross-turn facts aren't flagged as fabricated.
    prior_grounding_text: str = ""
    intent: Any = None
    agent: Any = None
    thread_id: str = ""
    approval_provider: Any = None
    output_chunk_sink: Any = None
    router: Any = None
    metadata: dict = field(default_factory=dict)
    is_goal_mode: bool = False
    observed_read_sequence: bool = False
    ordered_result_handoffs: bool = False
    realtime_public_orientation: bool = False
    realtime_public_narrative: bool = False
    # ── cfg · 6b model-call wiring (assembled once, read-only) ──
    temperature: float = 0.0
    max_tokens_per_iter: int = 0
    wants_thinking: bool = False
    reasoning_effort: Any = None
    native_evidence_update_tool_specs: list = field(default_factory=list)
    native_public_update_tool_specs: list = field(default_factory=list)
    budget_auto_pause_enabled: bool = False
    budget_pause_threshold: float = 0.0
    agent_id_for_pause: str = ""
    throughput_started_at: float = 0.0
    throughput_interval_s: float = 0.5
    # ── mode · turn flags (read-only in 6c) ──
    is_code_mode: bool = False
    browser_operation_mode: bool = False
    todo_protocol_required: bool = False
    todo_protocol_visible: bool = False
    file_inspection_tools_visible: bool = False
    read_only_turn: bool = False
    no_tool_turn: bool = False
    # ── guard · dsh repeat-tool-reminder (advisory, never vetoes) ──
    repeat_guard: Any = None
    guard_notices: list = field(default_factory=list)
    # ── convo · shared references (mutated in place, never re-synced) ──
    steps: list = field(default_factory=list)
    executed_beak_steps: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    working_set: dict = field(default_factory=dict)
    final_answer_segments: list = field(default_factory=list)
    # ── per-iteration synced scalars (synced in before 6c) ──
    tools_active: bool = False
    planning_mode: bool = False
    enable_tools: bool = True
    effective_model: str = ""
    current_phase: str = ""
    evidence_convergence_active: Any = None
    native_mode: bool = False
    model_failovers: int = 0
    model_timeout_recoveries: int = 0
    consecutive_format_violations: int = 0
    # Consecutive rounds that produced neither a tool call nor a final answer.
    # Drives ``ModelRequest.require_tool_use`` so a prose-only round is
    # answered by constraining the next decode rather than by another
    # prompt-level reminder the model is free to ignore.
    zero_action_rounds: int = 0
    throughput_chars: int = 0
    final_stream_started: bool = False
    force_convergence_next: bool = False
    # Sticky once repeated trusted verifier environment gaps require a
    # terminal, tools-disabled synthesis. Unlike the one-shot recovery flag,
    # this survives guard repair retries so tools cannot reappear.
    terminal_convergence_active: bool = False
    streamed_final_chars: int = 0
    progress_summary: str = ""
    public_progress_summary: str = ""
    consecutive_same_failed_actions: int = 0
    last_failed_action_fingerprint: str = ""
    # Safety net for "silent no-op" tools — the call returns ok=True but
    # produces no real effect (e.g. todo_write with a wrong key, search
    # with an empty query).  Without this the model can loop on the same
    # wrong shape indefinitely because the existing failed-action guard
    # only counts ok=False results.
    consecutive_same_noop_actions: int = 0
    last_noop_action_fingerprint: str = ""
    green_verification_convergence_active: bool = False
    green_convergence_todo_used: bool = False
    result_handoff_ready: bool = False
    last_public_update_key: str = ""
    saw_successful_code_write: bool = False
    clean_verification_rounds_after_write: int = 0
    quiet_evidence_steps: list = field(default_factory=list)
    throughput_last_emit: float = 0.0
    consecutive_llm_errors: int = 0
    # The main loop reads ``iteration_limit`` dynamically, allowing a
    # productive long-running turn to receive a bounded in-place extension
    # instead of being interrupted solely because it reached the initial
    # recipe limit. ``iteration_base_limit`` keeps each grant fixed-size so
    # repeated extensions do not grow exponentially.
    iteration_base_limit: int = 0
    iteration_limit: int = 0
    iteration_extensions_used: int = 0
    # Count of consecutive "blank" iterations where the model emitted no
    # tool call, no observation, no meaningful thought and no final answer
    # (e.g. degraded reasoning producing only whitespace). Used by the
    # model-spin guard to stop burning iterations early.
    consecutive_spin_iterations: int = 0
    # Capability-enhancing spin escalation: before pausing a spinning turn,
    # first force a context-compression pass, then attempt a model switch.
    # ``0`` = not yet escalated · ``1`` = compression forced · ``2`` = model
    # switch requested · ``3`` = exhausted, fall back to pause.
    spin_escalation_stage: int = 0
    # Set by the spin guard (phase 6g) when it decides the next escalation is
    # a model switch. The main loop consumes it after the cancel/pause guard
    # (before the next LLM call) and calls the model-failover closure.
    spin_model_switch_requested: bool = False
    # ── emit · terminal accumulators (synced in/out) ──
    final_answer: str | None = None
    terminated_reason: str = "max_iter"
    final_answer_emitted: bool = False
    final_delta_emitted_this_iteration: bool = False
    # ── parse · 6b/6c outputs consumed by later phases (synced out only) ──
    resp: Any = None
    raw_text: str = ""
    request_has_tool_evidence: bool = False
    iteration_soft_timed_out: bool = False
    maybe_emit_throughput: Any = None
    step: ReActStep | None = None
    maybe_final: str | None = None
    text: str = ""
    length_limited: bool = False
    length_limit_should_continue: bool = False
