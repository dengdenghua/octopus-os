from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from runtime.platform.models import (
    AntigenSignature,
    ArmId,
    CostEntry,
    ImmuneVerdict,
    Source,
    Step,
    TaskId,
    Trajectory,
    new_id,
    now_utc,
)

JournalEventType = Literal[
    "step",
    "trajectory",
    "immune",
    "budget_squirt",
    "budget_commit",
    "budget_breaker_reset",
    "genome_patch",
    "reflex_hit",
    "task_started",
    "node_started",
    "task_checkpoint",
    "react_checkpoint",
    "tool_effect_intent",
    "tool_effect_reconciliation",
    "task_paused",
    "task_resumed",
    "token_usage",
    "file_op",
    "file_rollback",
    "preview_refresh",
    "skill_proposal_decision",
    "curriculum_goal_decision",
    "mcp_proposal_decision",
    "protocol_drift_decision",
    "sub_tool_start",
    "sub_tool_end",
    "sub_text_delta",
    "sub_session_summary",
    "browser_artifact",
    "goal_change",
    "user/message",
    "assistant/chunk",
    "hook/invoked",
    "hook/result",
    "workflow/start",
    "workflow/progress",
    "workflow/end",
    "job/change",
]


# Event schema version. Bump when any event shape changes in a way
# that isn't purely additive (e.g. field rename, type narrowing,
# required-field addition). Readers honor this via `_EVENT_MIGRATIONS`
# below — old events parse through migration adapters to the current
# shape. The goal: past jsonl journals survive refactors.
#
# Version history:
#   1 — initial versioned schema (2026-04-19). Prior events lack
#       the field entirely; reader treats them as v1 since the shape
#       only changed additively up to this point. Any NEW breaking
#       change should introduce v2 and register a migration in
#       `_EVENT_MIGRATIONS`.
CURRENT_SCHEMA_VERSION = 1


class JournalEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = CURRENT_SCHEMA_VERSION
    event_id: UUID = Field(default_factory=new_id)
    event_type: JournalEventType
    task_id: TaskId | None = None
    arm_id: ArmId | None = None
    actor: str | None = None
    # Tenant ownership is part of the event envelope so JSONL and every
    # derived read model carry the same authorization context.  Existing
    # events omit these fields and are treated as legacy during migration.
    tenant_id: str | None = None
    owner_actor_id: str | None = None
    agent_id: str | None = None
    conversation_id: str | None = None
    ts: datetime = Field(default_factory=now_utc)
    source: Source | None = None


class StepEvent(JournalEvent):
    event_type: Literal["step"] = "step"
    step: Step


class TrajectoryEvent(JournalEvent):
    event_type: Literal["trajectory"] = "trajectory"
    trajectory: Trajectory


class ImmuneEvent(JournalEvent):
    event_type: Literal["immune"] = "immune"
    verdict: ImmuneVerdict
    signature: AntigenSignature
    reason: str = ""


class BudgetEvent(JournalEvent):
    event_type: Literal["budget_squirt", "budget_commit"] = "budget_commit"
    reason: str = ""
    cost: CostEntry = Field(default_factory=CostEntry)


class BudgetBreakerResetEvent(JournalEvent):
    """Operator reset for a derived budget/circuit-breaker component."""

    event_type: Literal["budget_breaker_reset"] = "budget_breaker_reset"
    component: str = ""
    reason: str = ""


class TaskStartedEvent(JournalEvent):
    event_type: Literal["task_started"] = "task_started"
    total_nodes: int = 0
    strategy: str = ""
    task_type: str = ""
    recipe_hash: str | None = None


class NodeStartedEvent(JournalEvent):
    event_type: Literal["node_started"] = "node_started"
    node_id: str = ""
    skill_ref: str = ""
    node_index: int = 0  # Implementation note.


class TaskCheckpointEvent(JournalEvent):
    event_type: Literal["task_checkpoint"] = "task_checkpoint"
    nodes_completed: int = 0
    total_nodes: int = 0
    tokens_spent: int = 0
    usd_spent: float = 0.0


class ReactCheckpointEvent(JournalEvent):
    """ReAct iteration checkpoint · written after each completed
    thought→action→observation cycle so a crashed/refreshed session
    can resume from the last good iteration.

    ``messages_snapshot`` is the serialized LLM message list at the
    end of this iteration (system + history + all prior
    thought/observation pairs). ``steps_snapshot`` carries the
    structured ``ReActStep`` dicts accumulated so far.

    ``working_set_snapshot`` carries the set of files the agent has
    read or modified, so a resumed agent knows which files are in
    play without re-reading them all.

    ``progress_summary`` is a short human-readable summary of what
    has been accomplished so far, injected into the resumed agent's
    system prompt so it can pick up context quickly.
    """

    event_type: Literal["react_checkpoint"] = "react_checkpoint"
    iteration_completed: int = 0
    max_iterations: int = 8
    messages_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    steps_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    has_final_answer: bool = False
    final_answer: str = ""
    working_set_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    progress_summary: str = ""
    current_phase: str = ""


class ToolEffectIntentEvent(JournalEvent):
    """Durable write-ahead marker for one tool invocation.

    The marker is appended immediately before entering a handler. If the
    process dies before its :class:`StepEvent` is written, recovery knows the
    side effect is indeterminate and must not blindly execute it again.
    """

    event_type: Literal["tool_effect_intent"] = "tool_effect_intent"
    effect_key: str
    call_id: str
    step_id: int = 0
    node_id: str = ""
    sucker_id: str = ""
    args_fingerprint: str = ""
    side_effecting: bool = False


class ToolEffectReconciliationEvent(JournalEvent):
    """Auditable operator decision for an indeterminate external effect."""

    event_type: Literal["tool_effect_reconciliation"] = "tool_effect_reconciliation"
    effect_key: str
    fencing_token: int
    action: Literal["authorize_retry"] = "authorize_retry"
    reason: str


class TaskPausedEvent(JournalEvent):
    event_type: Literal["task_paused"] = "task_paused"
    reason: str = "user_request"
    requested_by: str = ""
    iteration: int = 0


class TaskResumedEvent(JournalEvent):
    event_type: Literal["task_resumed"] = "task_resumed"
    resumed_by: str = ""
    extra_tokens: int = 0
    extra_usd: float = 0.0
    extra_iterations: int = 0


class TokenUsageEvent(JournalEvent):
    event_type: Literal["token_usage"] = "token_usage"
    session_id: str = ""
    iteration: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""


class FileOpEvent(JournalEvent):
    event_type: Literal["file_op"] = "file_op"
    path: str = ""
    action: Literal["create", "write", "edit", "delete", "rename"] = "write"
    old_size: int | None = None
    new_size: int | None = None
    bytes_delta: int = 0
    sucker_id: str = ""
    diff: str | None = None
    rollback: dict[str, Any] | None = None


class FileRollbackEvent(JournalEvent):
    event_type: Literal["file_rollback"] = "file_rollback"
    dry_run: bool = False
    project_root: str = ""
    event_id_filter: str | None = None
    task_id_filter: str | None = None
    path_filter: str | None = None
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    source_event_ids: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PreviewRefreshEvent(JournalEvent):
    event_type: Literal["preview_refresh"] = "preview_refresh"
    target: str = ""
    trigger_path: str = ""
    reason: str = ""


class ReflexHitEvent(JournalEvent):
    event_type: Literal["reflex_hit"] = "reflex_hit"
    rule_id: str = ""
    kind: str = "regex"  # regex / deterministic / cache / slm
    latency_ms: float = 0.0
    intent_goal: str = ""
    response: Any = None  # Implementation note.


class SkillProposalDecisionEvent(JournalEvent):
    """Operator decision for a self-evolution skill proposal."""

    event_type: Literal["skill_proposal_decision"] = "skill_proposal_decision"
    proposal_kind: str = "skill_forge"
    proposal_name: str = ""
    candidate_id: str = ""
    decision: str = ""
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class GoalChangeEvent(JournalEvent):
    """Durable CAS-guarded goal mutation (dsh ``goal/change``).

    ``change`` carries the raw dsh change dict — full next snapshot or clear
    tombstone with kind/version/operation — so the pure fold in
    ``runtime.memory.goals.fold`` can decode it losslessly on replay.
    """

    event_type: Literal["goal_change"] = "goal_change"
    change: dict[str, Any] = Field(default_factory=dict)


class UserMessageEvent(JournalEvent):
    """Durable human message (dsh ``user/message``).

    ``goal_source`` carries dsh ``GoalMessageSource`` attribution
    (``{"kind": "goal", "goalId": ..., "revision": ..., "round": ...}``)
    when the message continues an active goal; the goal fold validates
    it as the exact next admitted round. Messages without a source are
    plain transcript entries the fold ignores.

    ``session_id`` correlates a message to a durable sub-agent session
    (empty for parent/goal-level messages); a session-scoped message is
    still source-less, so the goal fold keeps ignoring it.
    """

    event_type: Literal["user/message"] = "user/message"
    session_id: str = ""
    text: str = ""
    goal_source: dict[str, Any] | None = None


class CurriculumGoalDecisionEvent(JournalEvent):
    """Operator decision for a journal-derived learning goal."""

    event_type: Literal["curriculum_goal_decision"] = "curriculum_goal_decision"
    goal_id: int = 0
    cluster_key: str = ""
    status: str = ""
    covered_by: str | None = None
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class McpProposalDecisionEvent(JournalEvent):
    """Operator/vet decision for a suggested MCP capability."""

    event_type: Literal["mcp_proposal_decision"] = "mcp_proposal_decision"
    server_name: str = ""
    status: str = ""
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ProtocolDriftDecisionEvent(JournalEvent):
    """Operator decision for a detected protocol drift event."""

    event_type: Literal["protocol_drift_decision"] = "protocol_drift_decision"
    drift_id: int = 0
    protocol_id: str = ""
    status: str = ""
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class SubToolStartEvent(JournalEvent):
    """Emitted when a sub-agent begins a tool call.

    Mirrors the ``sub_tool_start`` shape the SSE pump streams to
    the UI (see ``ephemeral_runner._emit_sub_tool_event``). Pushed
    through the journal so any subscriber — SSE pump, observability
    panel, persistent log — sees it without separate plumbing.
    """

    event_type: Literal["sub_tool_start"] = "sub_tool_start"
    agent_id: str = ""
    codename: str = ""
    avatar: str = ""
    role_id: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    iteration: int = 0
    args_preview: str = ""
    parent_tool_use_id: str | None = None


class SubToolEndEvent(JournalEvent):
    """Emitted when a sub-agent finishes a tool call."""

    event_type: Literal["sub_tool_end"] = "sub_tool_end"
    agent_id: str = ""
    codename: str = ""
    avatar: str = ""
    role_id: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    iteration: int = 0
    is_error: bool = False
    duration_ms: int = 0
    output_preview: str = ""
    parent_tool_use_id: str | None = None


class AssistantChunkEvent(JournalEvent):
    """One streamed parent-reply chunk (dsh ``assistant/chunk``).

    Mirrors the react loop's ``text_delta`` events: every user-visible
    final-answer fragment the loop releases lands here, so the
    assistant's streamed text is reconstructable from the journal
    alone (dsh session-log invariant). ``kind`` mirrors dsh's
    ``StreamChunk`` lane — ``"text-delta"`` today; future
    ``"reasoning-delta"`` / ``"tool-call-delta"`` lanes slot in
    without a schema change.
    """

    event_type: Literal["assistant/chunk"] = "assistant/chunk"
    iteration: int = 0
    kind: str = "text-delta"
    delta: str = ""
    # dsh ``tool-call-delta`` lane: optional call identity on the raw
    # fragments (parallel-call slot, stable provider call id, function
    # name once known). Only populated for ``kind == "tool-call-delta"``
    # and kept out of the packable common/extra shape when unset, so
    # text/reasoning runs pack exactly as before.
    index: int | None = None
    call_id: str = ""
    name: str = ""


class HookInvokedEvent(JournalEvent):
    """One external command hook invocation (dsh ``hook/invoked``).

    Log-only audit row, not a surface event: names the hook point
    (``PreToolUse`` / ``Stop`` / …), the bridge dialect that ran it, and a
    stable ``handler_id`` that correlates it with the paired
    :class:`HookResultEvent`. ``matcher`` is omitted when the hook matched
    all (dsh omits the field for match-all). ``turn_id`` ties the pair to
    the runtime turn whose lifecycle fired the hook; ``session_id`` is the
    runtime session id from the hook payload.
    """

    event_type: Literal["hook/invoked"] = "hook/invoked"
    session_id: str = ""
    turn_id: str = ""
    point: str = ""
    dialect: str = ""
    handler_id: str = ""
    matcher: str | None = None


class HookResultEvent(JournalEvent):
    """The durable outcome paired with :class:`HookInvokedEvent` (dsh
    ``hook/result``).

    ``decision`` is the decoded hook decision, ``stop`` when the hook asked
    to halt, else ``pass`` (dsh ``appendHookResult``); ``exit_code`` is
    omitted when the process never produced one (infra fault / timeout);
    ``stderr_summary`` is trimmed and capped at the bridge's
    ``stderrSummaryMaxChars``; ``duration_ms`` is the wall-clock run time.
    """

    event_type: Literal["hook/result"] = "hook/result"
    session_id: str = ""
    turn_id: str = ""
    point: str = ""
    handler_id: str = ""
    decision: str = ""
    exit_code: int | None = None
    stderr_summary: str | None = None
    duration_ms: int = 0


class SubTextDeltaEvent(JournalEvent):
    """One streamed role-prose chunk (dsh ``assistant/chunk``).

    Mirrors the ``sub_text_delta`` shape the SSE pump streams to the
    UI (see ``ephemeral_runner``). Journaling every chunk makes the
    sub-agent's streaming prose reconstructable from the log —
    the dsh session-log invariant "model-visible means logged" —
    instead of living only in the in-memory emitter callback.

    ``session_id`` carries the durable sub-agent session (when the child
    is continuable) so a session's streamed prose is filterable per
    session, not only per ``role_id`` — ``role_id`` is the agent/role id
    shared by every session of the same role.
    """

    event_type: Literal["sub_text_delta"] = "sub_text_delta"
    session_id: str = ""
    agent_id: str = ""
    codename: str = ""
    avatar: str = ""
    role_id: str = ""
    round: int = 0
    delta: str = ""
    parent_tool_use_id: str | None = None


class SubSessionSummaryEvent(JournalEvent):
    """One completed turn's outcome row for a durable sub-agent session.

    Complementary to the per-chunk ``sub_text_delta`` and per-prompt
    ``user/message`` rows: it records the structured completion facts a
    resume path needs without replaying every chunk — ``rounds`` spent,
    whether the turn succeeded, and any error. ``session_id`` correlates it
    to the durable session (dsh session-log invariant: the session's story,
    including its outcome, is reconstructable from the log alone).
    """

    event_type: Literal["sub_session_summary"] = "sub_session_summary"
    session_id: str = ""
    agent_id: str = ""
    rounds: int = 0
    success: bool = True
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class BrowserArtifactEvent(JournalEvent):
    """A browser screenshot (or similar artifact) was produced.

    Saved to disk by ``browser_act_skills._emit_screenshot_artifact``.
    This event lets the SSE pump deliver the artifact inline in the
    chat stream, so screenshots appear as they're
    captured rather than only showing up in a separate panel.
    """

    event_type: Literal["browser_artifact"] = "browser_artifact"
    kind: str = "screenshot"
    url: str = ""
    filename: str = ""
    caption: str = ""
    mime_type: str = "image/png"
    width: int | None = None
    height: int | None = None
    thread_id: str = ""


class WorkflowStartEvent(JournalEvent):
    """A model-authored orchestration script started (dsh workflow
    ``on_start``). ``run_id`` correlates every later row of the same run;
    the event groups under the parent thread's ``task_id`` so the
    conversation timeline carries the run's lifecycle."""

    event_type: Literal["workflow/start"] = "workflow/start"
    run_id: str = ""
    name: str = ""
    description: str = ""


class WorkflowProgressEvent(JournalEvent):
    """One workflow narration row (dsh workflow observer): a phase, a log
    line, or an agent start/end. ``kind`` is one of ``phase`` / ``log`` /
    ``agent_start`` / ``agent_end``; agent rows carry ``seq`` and ``label``."""

    event_type: Literal["workflow/progress"] = "workflow/progress"
    run_id: str = ""
    kind: str = ""
    text: str = ""
    agent_seq: int = 0
    agent_label: str = ""


class WorkflowEndEvent(JournalEvent):
    """A workflow run settled (dsh workflow ``on_end`` / settlement)."""

    event_type: Literal["workflow/end"] = "workflow/end"
    run_id: str = ""
    stop_reason: str = ""
    agents_started: int = 0
    error: str = ""


class JobChangeEvent(JournalEvent):
    """One background-job lifecycle transition (dsh ``tool-jobs``): start,
    stop request, or terminal settlement. ``status`` mirrors the registry's
    ``running`` / ``stopping`` / ``completed`` / ``killed`` / ``failed``."""

    event_type: Literal["job/change"] = "job/change"
    job_id: str = ""
    kind: str = ""
    label: str = ""
    status: str = ""
    detail: str = ""
