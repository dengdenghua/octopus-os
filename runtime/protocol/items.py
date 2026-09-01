"""Item-oriented state model.

A ``Turn`` is an ordered list of ``Item`` instances. Every observable
agent output — assistant text, reasoning, a shell command, a file edit,
an MCP tool call — is one ``Item``. Lifecycle is uniform:

  1. Server emits ``item/started`` carrying the initial Item snapshot.
  2. Server emits zero or more subtype-specific delta notifications
     (``item/agentMessage/delta``, ``item/commandExecution/outputDelta``,
     etc.) keyed by ``itemId``.
  3. Server emits ``item/completed`` with the final Item snapshot.

Clients reduce by ``itemId``: a delta merges into the in-flight item,
``completed`` replaces it. Out-of-order or duplicated events stay safe
because each event carries the full identity (threadId, turnId, itemId).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from runtime.platform.models.primitives import new_id, now_utc


class ItemStatus(StrEnum):
    IN_PROGRESS = "inProgress"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    DECLINED = "declined"


class ItemType(StrEnum):
    USER_MESSAGE = "userMessage"
    STEERING_USER_MESSAGE = "steeringUserMessage"
    AGENT_MESSAGE = "agentMessage"
    REASONING = "reasoning"
    PLAN = "plan"
    TODO_LIST = "todo-list"
    COMMAND_EXECUTION = "commandExecution"
    FILE_CHANGE = "fileChange"
    MCP_TOOL_CALL = "mcpToolCall"
    SUBAGENT = "subagent"
    APPROVAL = "approval"
    VERIFICATION = "verification"
    ARTIFACT = "artifact"
    ERROR = "error"
    VISIBILITY = "visibility"


class ItemMarker(StrEnum):
    """Magic ``McpToolCallItem.tool`` values that flag synthesised
    lifecycle items.

    These aren't real MCP tool calls; the bridge writes them so the
    frontend can render a sub-agent tile from the moment the agent
    spawns instead of waiting for its first ``sub_tool_*`` event.
    The frontend's ``mcpItemToLiveEvent`` recognises these markers
    and emits a ``LiveToolEvent`` with ``lifecycle: "spawned" |
    "finished"`` instead of a tool row.
    """

    SUBAGENT_SPAWNED = "__subagent_spawned__"
    SUBAGENT_FINISHED = "__subagent_finished__"


# ── Base item ─────────────────────────────────────────────────


class _ItemBase(BaseModel):
    """Common fields on every item.

    ``id`` is server-assigned and must be globally unique within the
    turn. ``created_at`` is the server's wall clock at start.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: f"itm_{new_id().hex[:16]}")
    type: ItemType
    status: ItemStatus = ItemStatus.IN_PROGRESS
    created_at: datetime = Field(default_factory=now_utc, alias="createdAt")
    # Stable coordinates in the public turn timeline. These fields live on
    # every item instead of only commentary so replay/reconnect clients can
    # restore causal order without inferring it from arrival timing or text.
    # They remain optional for backward compatibility with existing logs.
    timeline_sequence: int | None = Field(default=None, alias="timelineSequence")
    parent_item_id: str | None = Field(default=None, alias="parentItemId")
    phase_id: str | None = Field(default=None, alias="phaseId")


class UserMessageItem(_ItemBase):
    type: Literal[ItemType.USER_MESSAGE] = ItemType.USER_MESSAGE
    text: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class SteeringUserMessageItem(_ItemBase):
    """Optimistic user injection while a turn is running.

    Inserted by the client before the server has acknowledged it.
    Status flips ``IN_PROGRESS → COMPLETED`` when the server finally
    delivers the corresponding ``item/completed`` for this id.
    """

    type: Literal[ItemType.STEERING_USER_MESSAGE] = ItemType.STEERING_USER_MESSAGE
    text: str
    target_turn_id: str | None = Field(default=None, alias="targetTurnId")
    # Human steering is rendered as a user message. Internal child reports use
    # the same live queue for model context but must remain inside the owning
    # assistant turn rather than manufacturing new visible user turns.
    source: Literal["user", "subagent_report"] = "user"


class AgentMessageItem(_ItemBase):
    type: Literal[ItemType.AGENT_MESSAGE] = ItemType.AGENT_MESSAGE
    text: str = ""
    # A commentary message is a concrete user-facing checkpoint between tool
    # rounds, not the terminal answer. It reuses the normal message transport.
    message_kind: Literal["answer", "commentary"] = Field(
        default="answer",
        alias="messageKind",
    )
    # ``progress_sequence`` is the legacy commentary-only counter. New clients
    # should prefer the common ``timeline_sequence`` inherited from _ItemBase.
    progress_sequence: int | None = Field(default=None, alias="progressSequence")
    # Per-message speaker identity. Set in group/team rooms so each bubble
    # renders the ACTUAL author's avatar + name (not the turn leader's). The
    # frontend reads these off the message's additional_kwargs; when unset it
    # falls back to the turn's agent — so single-agent turns are unaffected.
    agent_display_name: str | None = Field(default=None, alias="agentDisplayName")
    agent_avatar_url: str | None = Field(default=None, alias="agentAvatarUrl")
    agent_icon: str | None = Field(default=None, alias="agentIcon")
    # ③ @因果链：本气泡回应/反驳的成员 display name，前端在气泡标题旁显示
    # "回应 @谁"。
    reply_to: str | None = Field(default=None, alias="replyTo")


class ReasoningItem(_ItemBase):
    type: Literal[ItemType.REASONING] = ItemType.REASONING
    summary: list[str] = Field(default_factory=list)
    content: str = ""
    # Wall-clock thinking time from first reasoning_delta to item completion.
    # Filled by the realtime bridge on _emit_completed; None for legacy data
    # and for streams that never received a completion event.
    duration_ms: int | None = Field(default=None, alias="durationMs")


class PlanItem(_ItemBase):
    type: Literal[ItemType.PLAN] = ItemType.PLAN
    text: str = ""


class TodoEntry(BaseModel):
    title: str
    status: Literal["pending", "in_progress", "completed", "blocked"] = "pending"


class TodoListItem(_ItemBase):
    type: Literal[ItemType.TODO_LIST] = ItemType.TODO_LIST
    explanation: str | None = None
    plan: list[TodoEntry] = Field(default_factory=list)
    objective_id: str | None = Field(default=None, alias="objectiveId")
    task_id: str | None = Field(default=None, alias="taskId")


class AgentPhaseSnapshot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    index: int
    total: int
    title: str
    detail: str | None = None
    status: Literal["pending", "running", "done", "error", "waiting_approval"]
    active_item_id: str | None = Field(default=None, alias="activeItemId")
    # Coarse business phase (planning/exploring/implementing/testing/deploying/other)
    # mapped from the todo title. Lets the frontend render a localized label
    # instead of the raw technical wording. "other" = no match.
    phase_kind: str = "other"


class WorkspaceFocus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_id: str = Field(alias="itemId")
    view: Literal[
        "trace",
        "terminal",
        "browser",
        "diff",
        "file",
        "artifact",
        "image",
        "approval",
        "subagent",
    ]
    title: str
    subtitle: str | None = None
    preview_url: str | None = Field(default=None, alias="previewUrl")


class EvidenceReference(BaseModel):
    """One confirmed source or result supporting the current workbench state.

    Unlike raw tool output, this survives replay without asking the client to
    reverse-engineer filenames or success from presentation text.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: Literal["file", "web", "verification", "artifact"]
    title: str
    uri: str | None = None
    status: Literal["observed", "pending", "passed", "failed"] = "observed"
    origin: Literal["grounding", "tool", "verification", "artifact"]
    source_item_id: str | None = Field(default=None, alias="sourceItemId")
    phase_id: str | None = Field(default=None, alias="phaseId")
    detail: str | None = None


class WorkbenchSnapshotV2(BaseModel):
    """Current workbench frame for a turn.

    This wraps the existing phase/focus state in a versioned envelope so
    realtime views and replay views can render the same single current
    frame instead of reconstructing it differently from raw event history.

    ``version`` semantics:
        Per-turn monotonically increasing counter, **NOT** a global thread
        sequence. Each new turn starts from 0 because the bridge state
        (``_ReactBridgeState``) is rebuilt per-turn. Clients should treat
        ``version`` as a within-turn ordering hint for late-arriving
        snapshots, not as a thread-wide replay cursor.
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal[2] = Field(default=2, alias="schemaVersion")
    version: int
    status: Literal["pending", "running", "done", "error", "waiting_approval"]
    phases: list[AgentPhaseSnapshot] = Field(default_factory=list)
    current_phase_id: str | None = Field(default=None, alias="currentPhaseId")
    current_item_id: str | None = Field(default=None, alias="currentItemId")
    workspace_focus: WorkspaceFocus | None = Field(default=None, alias="workspaceFocus")
    evidence: list[EvidenceReference] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now_utc, alias="updatedAt")


class CommandAction(BaseModel):
    cmd: str
    cwd: str | None = None
    kind: str = "shell"


class ToolEffectSignal(BaseModel):
    """Small operator-safe receipt carried beside a realtime tool item."""

    model_config = ConfigDict(populate_by_name=True)

    effect_key: str = Field(alias="effectKey")
    call_id: str = Field(alias="callId")
    state: Literal["indeterminate"]
    reason: str
    fencing_token: int = Field(default=0, alias="fencingToken", ge=0)


class CommandExecutionItem(_ItemBase):
    type: Literal[ItemType.COMMAND_EXECUTION] = ItemType.COMMAND_EXECUTION
    command: str
    input_preview: Any | None = Field(default=None, alias="inputPreview")
    actions: list[CommandAction] = Field(default_factory=list)
    cwd: str | None = None
    aggregated_output: str = Field(default="", alias="aggregatedOutput")
    exit_code: int | None = Field(default=None, alias="exitCode")
    process_id: str | None = Field(default=None, alias="processId")
    network_access: bool = Field(default=False, alias="networkAccess")
    effect_receipt: ToolEffectSignal | None = Field(default=None, alias="effectReceipt")


class FileHunk(BaseModel):
    """A single contiguous change within a file.

    One hunk maps to one ``@@`` section in a unified diff. Making hunks
    first-class on the wire lets the UI render per-hunk accept/reject
    controls without re-parsing the raw diff, and lets the server emit
    large patches incrementally (``item/fileChange/hunkDelta``) instead
    of holding the whole diff until completion.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: f"hnk_{new_id().hex[:12]}")
    old_start: int = Field(default=0, alias="oldStart")
    old_lines: int = Field(default=0, alias="oldLines")
    new_start: int = Field(default=0, alias="newStart")
    new_lines: int = Field(default=0, alias="newLines")
    # Body preserves the ``" "``/``"-"``/``"+"`` prefix per line so the
    # UI can render inline diff without re-tokenising.
    body: str = ""
    # Transient UI-level decision: pending / accepted / rejected.
    # The server's authoritative state is whatever was written to disk;
    # this field mirrors what the user has ACKed so the client can
    # render visual state across reconnects.
    decision: Literal["pending", "accepted", "rejected"] = "pending"


class FileChange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str
    op: Literal["create", "update", "delete"]
    # Raw unified diff is kept for backward compatibility with the
    # flat ``revert-diff`` REST endpoint and for tooling that prefers
    # the textual form. New UIs should drive off ``hunks``.
    diff: str | None = None
    # True when ``diff`` was cut at the executor's output limit. A
    # truncated diff under-counts +/- lines and cannot be reverse-
    # applied (it may end mid-hunk) — clients must label it and keep
    # it out of revert paths. ``hunks`` stream separately and are
    # unaffected.
    diff_truncated: bool = Field(default=False, alias="diffTruncated")
    hunks: list[FileHunk] = Field(default_factory=list)


# The executor appends this marker when a unified diff exceeds its
# output limit (``_compute_unified_diff`` in
# ``runtime/execution/tool_engine/executor.py``). The format is a wire
# contract: ``diff_is_truncated`` is how downstream FileChange builders
# recover the flag from the in-band text, so change both together.
_DIFF_TRUNCATION_RE = re.compile(r"\.\.\. \(truncated \d+ bytes\)\s*$")


def diff_is_truncated(diff: str | None) -> bool:
    """True when ``diff`` ends with the executor's truncation marker."""
    return bool(diff) and _DIFF_TRUNCATION_RE.search(diff or "") is not None


class FileChangeItem(_ItemBase):
    type: Literal[ItemType.FILE_CHANGE] = ItemType.FILE_CHANGE
    changes: list[FileChange] = Field(default_factory=list)
    grant_root: str | None = Field(default=None, alias="grantRoot")


class McpToolProgress(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str | None = None
    status: Literal["queued", "running", "done", "error"] | None = None
    percent: float | int | None = None
    current: float | int | None = None
    total: float | int | None = None
    preview: Any | None = None
    updated_at: datetime = Field(default_factory=now_utc, alias="updatedAt")


class McpToolCallItem(_ItemBase):
    type: Literal[ItemType.MCP_TOOL_CALL] = ItemType.MCP_TOOL_CALL
    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any | None = None
    error: str | None = None
    duration_ms: int | None = Field(default=None, alias="durationMs")
    progress: McpToolProgress | None = None


class SubagentItem(_ItemBase):
    """First-class lifecycle record for delegated agent work.

    The current bridge still emits ``ItemMarker`` MCP placeholders for
    backward compatibility. New runtimes should prefer this item so UI
    panels can render an agent run tree without decoding magic tool names.
    """

    type: Literal[ItemType.SUBAGENT] = ItemType.SUBAGENT
    subagent_id: str = Field(alias="subagentId")
    role: str | None = None
    name: str | None = None
    codename: str | None = None
    avatar: str | None = None
    summary: str | None = None
    error: str | None = None
    iteration_count: int | None = Field(default=None, alias="iterationCount")
    files_touched: list[str] = Field(default_factory=list, alias="filesTouched")


class ApprovalItem(_ItemBase):
    """Human-in-the-loop request captured as conversation state.

    WebSocket request/response remains the transport mechanism; this item
    makes the pending decision replayable after reconnect and visible in
    durable turn history.
    """

    type: Literal[ItemType.APPROVAL] = ItemType.APPROVAL
    request_id: str = Field(alias="requestId")
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    target_item_id: str | None = Field(default=None, alias="targetItemId")
    decision: Literal["pending", "accepted", "declined"] = "pending"
    resolved_at: datetime | None = Field(default=None, alias="resolvedAt")


class VerificationItem(_ItemBase):
    """Result of a validation command associated with code changes."""

    type: Literal[ItemType.VERIFICATION] = ItemType.VERIFICATION
    command: str
    kind: Literal["test", "lint", "typecheck", "build", "diagnostic", "manual"] = "manual"
    exit_code: int | None = Field(default=None, alias="exitCode")
    summary: str | None = None
    stdout_tail: str | None = Field(default=None, alias="stdoutTail")
    stderr_tail: str | None = Field(default=None, alias="stderrTail")
    related_files: list[str] = Field(default_factory=list, alias="relatedFiles")
    related_change_item_ids: list[str] = Field(default_factory=list, alias="relatedChangeItemIds")


class VisibilityItem(_ItemBase):
    """Snapshot of the turn's decision-point visibility trace.

    Carries the trace export collected during turn assembly — capability
    routing, delegation-tool visibility and skill-catalog decisions — each
    as a step with the conclusion and the basis behind it. Emitted once
    per turn as a snapshot item (started + completed back-to-back, no
    deltas) so clients can render a "why these choices" panel without
    re-walking raw events.
    """

    type: Literal[ItemType.VISIBILITY] = ItemType.VISIBILITY
    summary: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)


class ArtifactItem(_ItemBase):
    """Generated or modified artifact with preview and validation state."""

    type: Literal[ItemType.ARTIFACT] = ItemType.ARTIFACT
    artifact_id: str = Field(alias="artifactId")
    kind: Literal[
        "code", "docx", "xlsx", "pptx", "pdf", "image", "html", "log", "trace", "other"
    ] = "other"
    path: str
    mime_type: str | None = Field(default=None, alias="mimeType")
    title: str | None = None
    version: int | None = None
    created_by_item_id: str | None = Field(default=None, alias="createdByItemId")
    preview_url: str | None = Field(default=None, alias="previewUrl")
    render_status: Literal["notRendered", "rendering", "rendered", "failed"] = Field(
        default="notRendered",
        alias="renderStatus",
    )
    validation_status: Literal["unknown", "pending", "passed", "failed"] = Field(
        default="unknown",
        alias="validationStatus",
    )


class ErrorItem(_ItemBase):
    type: Literal[ItemType.ERROR] = ItemType.ERROR
    status: ItemStatus = ItemStatus.FAILED
    message: str
    will_retry: bool = Field(default=False, alias="willRetry")
    error_info: dict[str, Any] | None = Field(default=None, alias="errorInfo")


Item: TypeAlias = Annotated[
    UserMessageItem
    | SteeringUserMessageItem
    | AgentMessageItem
    | ReasoningItem
    | PlanItem
    | TodoListItem
    | CommandExecutionItem
    | FileChangeItem
    | McpToolCallItem
    | SubagentItem
    | ApprovalItem
    | VerificationItem
    | VisibilityItem
    | ArtifactItem
    | ErrorItem,
    Field(discriminator="type"),
]


# ── Turn ──────────────────────────────────────────────────────


class TurnStatus(StrEnum):
    IN_PROGRESS = "inProgress"
    COMPLETED = "completed"
    # A durable, resumable stop.  This is intentionally distinct from a
    # user cancellation or a transport interruption: PAUSED owns a
    # checkpoint and can continue the same objective/task identity.
    PAUSED = "paused"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class GroundingSource(BaseModel):
    """One project reference folded into the model context for this turn."""

    kind: Literal["doc", "source"]
    title: str
    path: str


class TurnParams(BaseModel):
    """Inputs to :meth:`Runtime.start_turn`. Carries only the fields
    echo actually consumes; the surface is intentionally narrow
    so the rest of the protocol can evolve freely."""

    model_config = ConfigDict(populate_by_name=True)

    thread_id: str = Field(alias="threadId")
    # Client-stable id for the one first-class userMessage item created by a
    # new turn.  Keeping the optimistic UI row and durable server item on the
    # same coordinate makes replay/reconnect reduction idempotent.  The
    # namespace is deliberately narrower than arbitrary Item ids because this
    # value crosses a trust boundary.
    user_item_id: str | None = Field(
        default=None,
        alias="userItemId",
        min_length=8,
        max_length=96,
        pattern=r"^itm_[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    input: list[dict[str, Any]] = Field(default_factory=list)
    cwd: str | None = None
    approval_policy: Literal["never", "on-request", "untrusted"] = Field(
        default="on-request",
        alias="approvalPolicy",
    )
    sandbox_policy: dict[str, Any] = Field(
        default_factory=lambda: {"type": "workspaceWrite", "networkAccess": False},
        alias="sandboxPolicy",
    )
    model: str | None = None
    effort: Literal["minimal", "low", "medium", "high", "xhigh", "max"] = "medium"
    summary: Literal["none", "auto", "detailed"] = "none"
    output_schema: dict[str, Any] | None = Field(default=None, alias="outputSchema")
    # Plan-first mode (renamed semantics 2026-05-31).
    #
    # When ``true`` the runtime nudges the model to write or update a
    # short ``plan.md`` (or ``todo_write`` entries) BEFORE substantial
    # tool work. Tool execution stays ENABLED — the model is expected
    # to keep going in the same turn and produce real results, not a
    # plan-only stub. The previous "stop after producing a plan, wait
    # for an approval round-trip" behaviour was removed because it
    # confused users (no progress visible, no tool calls, the
    # interrupt button looked stuck).
    #
    # Auto-detection (``_should_default_planning_mode`` in the
    # cerebrum) trips this flag for complex / research-shaped turns
    # so users don't have to opt in. Chat-style turns skip it.
    #
    # The ``exit_plan_mode`` skill is still available for cases where
    # the model wants explicit human-in-the-loop approval mid-turn,
    # but it is no longer the default flow.
    planning_mode: bool = Field(default=False, alias="planningMode")
    # Team-topology id (fingerprint, ``"name"`` or alias) selecting a
    # multi-agent recipe from the organization registry. When set,
    # the turn runs through ``TeamRunner`` instead of the single-agent
    # ReAct loop. Falls back to single-agent on unknown id.
    topology_id: str | None = Field(default=None, alias="topologyId")
    # Per-turn output-style overlay. Appends a short style-instruction
    # paragraph to the ReAct system prompt (same model, same tools, only
    # the closing instruction changes). One of ``"concise" | "detailed"
    # | "audit" | "review" | "default"``; ``None`` / ``"default"`` /
    # unknown values are no-ops. See
    # ``runtime.core.cerebrum.output_styles.render_output_style``.
    output_style: str | None = Field(default=None, alias="outputStyle")
    tenant_id: str | None = Field(default=None, exclude=True)
    owner_actor_id: str | None = Field(default=None, exclude=True)


class Turn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: f"trn_{new_id().hex[:16]}")
    thread_id: str = Field(alias="threadId")
    status: TurnStatus = TurnStatus.IN_PROGRESS
    started_at: datetime = Field(default_factory=now_utc, alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    items: list[Item] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    params: TurnParams | None = None
    grounding: list[GroundingSource] = Field(default_factory=list)
    phases: list[AgentPhaseSnapshot] = Field(default_factory=list)
    workspace_focus: WorkspaceFocus | None = Field(default=None, alias="workspaceFocus")
    workbench_snapshot: WorkbenchSnapshotV2 | None = Field(
        default=None,
        alias="workbenchSnapshot",
    )
    interrupt_reason: str | None = Field(default=None, alias="interruptReason")
    # Stable lifecycle coordinates.  ``turn.id`` is one UI/transport attempt;
    # a resumed objective can span several turns while retaining objectiveId
    # and taskId.
    objective_id: str | None = Field(default=None, alias="objectiveId")
    task_id: str | None = Field(default=None, alias="taskId")
    # Trusted runtime-only execution strand used by the evolution ledger.
    # It is never accepted from or serialized back to the client.
    execution_engine: str | None = Field(default=None, exclude=True)
    # Resolved cwd after authentication, local-workspace validation, and
    # managed-workspace allocation. Task supervision consumes this trusted
    # value instead of guessing from the client's raw TurnParams shape.
    execution_workspace_path: str | None = Field(default=None, exclude=True)
    checkpoint_id: int | None = Field(default=None, alias="checkpointId")
    outcome_reason: str | None = Field(default=None, alias="outcomeReason")
    # Canonical semantic result emitted by the agent loop. ``status`` remains
    # the compact transport lifecycle; this payload preserves distinctions
    # such as completed-with-warning and partial-but-resumable delivery.
    completion_decision: dict[str, Any] | None = Field(
        default=None,
        alias="completionDecision",
    )


__all__ = [
    "AgentPhaseSnapshot",
    "AgentMessageItem",
    "ApprovalItem",
    "ArtifactItem",
    "CommandAction",
    "CommandExecutionItem",
    "ErrorItem",
    "EvidenceReference",
    "FileChange",
    "FileChangeItem",
    "FileHunk",
    "GroundingSource",
    "Item",
    "ItemMarker",
    "ItemStatus",
    "ItemType",
    "McpToolCallItem",
    "McpToolProgress",
    "PlanItem",
    "ReasoningItem",
    "SteeringUserMessageItem",
    "SubagentItem",
    "TodoEntry",
    "TodoListItem",
    "Turn",
    "TurnParams",
    "TurnStatus",
    "UserMessageItem",
    "VerificationItem",
    "VisibilityItem",
    "WorkbenchSnapshotV2",
    "WorkspaceFocus",
]
