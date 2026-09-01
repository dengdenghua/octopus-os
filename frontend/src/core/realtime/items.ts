// Item-oriented state model — mirrors runtime/protocol/items.py.
//
// Items are the building blocks of conversation state. Every observable
// agent output (assistant text, reasoning, command, file change, …) is an
// Item with stable id and tagged ``type``. The realtime reducer rebuilds
// Conversation state from item/* notifications keyed on ``id``.

// Audit A-04: enums are generated from runtime/protocol/items.py by
// scripts/gen_realtime_protocol_enums.py — the source of truth is the
// Python definitions; this file imports them for its interfaces and
// re-exports them for consumers.
import {
  ITEM_STATUSES,
  ITEM_TYPES,
  TURN_STATUSES,
} from "./protocol-enums.generated";
import type {
  ItemStatus,
  ItemType,
  TurnStatus,
} from "./protocol-enums.generated";
export { ITEM_STATUSES, ITEM_TYPES, TURN_STATUSES };
export type { ItemStatus, ItemType, TurnStatus };

export interface ItemBase {
  id: string;
  type: ItemType;
  status: ItemStatus;
  createdAt: string;
  /** Stable causal position inside one turn. Older persisted items may omit it. */
  timelineSequence?: number | null;
  parentItemId?: string | null;
  phaseId?: string | null;
}

export interface UserMessageItem extends ItemBase {
  type: "userMessage";
  text: string;
  attachments?: Record<string, unknown>[];
}

export interface SteeringUserMessageItem extends ItemBase {
  type: "steeringUserMessage";
  text: string;
  targetTurnId: string | null;
  source?: "user" | "subagent_report";
}

export interface AgentMessageItem extends ItemBase {
  type: "agentMessage";
  text: string;
  messageKind?: "answer" | "commentary";
  progressSequence?: number;
  /** Per-message speaker identity (group/team rooms). When set, the bubble
   *  renders this member's avatar + name instead of the turn leader's. */
  agentDisplayName?: string;
  agentAvatarUrl?: string;
  agentIcon?: string;
  /** ③ @因果链：本气泡回应/反驳的成员 display name。 */
  replyTo?: string;
}

export interface ReasoningItem extends ItemBase {
  type: "reasoning";
  summary: string[];
  content: string;
  // Wall-clock thinking time from first reasoning_delta to item completion.
  // Filled by the backend on item/completed; null for legacy data and
  // streams that never received a completion event.
  durationMs: number | null;
}

export interface PlanItem extends ItemBase {
  type: "plan";
  text: string;
}

export interface TodoEntry {
  title: string;
  status: "pending" | "in_progress" | "completed" | "blocked";
}

export interface TodoListItem extends ItemBase {
  type: "todo-list";
  explanation: string | null;
  plan: TodoEntry[];
  objectiveId?: string | null;
  taskId?: string | null;
}

export interface AgentPhaseSnapshot {
  id: string;
  index: number;
  total: number;
  title: string;
  detail?: string | null;
  status: "pending" | "running" | "done" | "error" | "waiting_approval";
  activeItemId?: string | null;
  /** Backend-mapped business phase (planning/exploring/implementing/testing/
   * deploying/other). Optional — older backends omit it and the frontend
   * falls back to mapping the title locally. */
  phaseKind?: string | null;
}

export type WorkspaceFocusView =
  | "trace"
  | "terminal"
  | "browser"
  | "diff"
  | "file"
  | "artifact"
  | "image"
  | "approval"
  | "subagent";

export interface WorkspaceFocus {
  itemId: string;
  view: WorkspaceFocusView;
  title: string;
  subtitle?: string | null;
  previewUrl?: string | null;
}

export interface EvidenceReference {
  id: string;
  kind: "file" | "web" | "verification" | "artifact";
  title: string;
  uri?: string | null;
  status: "observed" | "pending" | "passed" | "failed";
  origin: "grounding" | "tool" | "verification" | "artifact";
  sourceItemId?: string | null;
  phaseId?: string | null;
  detail?: string | null;
}

export interface WorkbenchSnapshotV2 {
  schemaVersion: 2;
  version: number;
  status: "pending" | "running" | "done" | "error" | "waiting_approval";
  phases: AgentPhaseSnapshot[];
  currentPhaseId?: string | null;
  currentItemId?: string | null;
  workspaceFocus?: WorkspaceFocus | null;
  evidence?: EvidenceReference[];
  updatedAt: string;
}

export interface CommandExecutionItem extends ItemBase {
  type: "commandExecution";
  command: string;
  inputPreview?: unknown;
  cwd: string | null;
  aggregatedOutput: string;
  exitCode: number | null;
  processId: string | null;
  networkAccess: boolean;
  effectReceipt?: ToolEffectSignal | null;
}

export interface ToolEffectSignal {
  effectKey: string;
  callId: string;
  state: "indeterminate";
  reason: string;
  fencingToken: number;
}

export interface FileHunk {
  id: string;
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  body: string;
  decision: "pending" | "accepted" | "rejected";
}

export interface FileChange {
  path: string;
  op: "create" | "update" | "delete";
  diff?: string | null;
  /**
   * True when `diff` was cut at the server's output limit. A truncated
   * diff under-counts +/- lines and cannot be reverse-applied — label
   * it in the UI and keep it out of revert paths. `hunks` stream
   * separately and are unaffected.
   */
  diffTruncated?: boolean;
  hunks?: FileHunk[];
}

export interface FileChangeItem extends ItemBase {
  type: "fileChange";
  changes: FileChange[];
  grantRoot: string | null;
}

export interface McpToolCallItem extends ItemBase {
  type: "mcpToolCall";
  server: string;
  tool: string;
  arguments: Record<string, unknown>;
  result: unknown;
  error: string | null;
  durationMs: number | null;
  progress?: McpToolProgress | null;
}

export interface McpToolProgress {
  label?: string | null;
  status?: "queued" | "running" | "done" | "error";
  percent?: number | null;
  current?: number | null;
  total?: number | null;
  preview?: unknown;
  updatedAt: string;
}

export interface SubagentItem extends ItemBase {
  type: "subagent";
  subagentId: string;
  role: string | null;
  name: string | null;
  codename: string | null;
  avatar: string | null;
  summary: string | null;
  error: string | null;
  iterationCount: number | null;
  filesTouched: string[];
}

export interface ApprovalItem extends ItemBase {
  type: "approval";
  requestId: string;
  method: string;
  params: Record<string, unknown>;
  targetItemId: string | null;
  decision: "pending" | "accepted" | "declined";
  resolvedAt: string | null;
}

export interface VerificationItem extends ItemBase {
  type: "verification";
  command: string;
  kind: "test" | "lint" | "typecheck" | "build" | "diagnostic" | "manual";
  exitCode: number | null;
  summary: string | null;
  stdoutTail: string | null;
  stderrTail: string | null;
  relatedFiles: string[];
  relatedChangeItemIds: string[];
}

/** One decision point inside a visibility (capability routing / delegation /
 * skill-catalog) snapshot. Snapshot-only — no incremental deltas. */
export interface VisibilityStep {
  decision_point: string;
  conclusion: string;
  basis: string;
  ts: number;
  details?: Record<string, unknown>;
}

export interface VisibilityItem extends ItemBase {
  type: "visibility";
  summary: string;
  steps: VisibilityStep[];
}

export interface ArtifactItem extends ItemBase {
  type: "artifact";
  artifactId: string;
  kind:
    | "code"
    | "docx"
    | "xlsx"
    | "pptx"
    | "pdf"
    | "image"
    | "html"
    | "log"
    | "trace"
    | "other";
  path: string;
  mimeType: string | null;
  title: string | null;
  version: number | null;
  createdByItemId: string | null;
  previewUrl: string | null;
  renderStatus: "notRendered" | "rendering" | "rendered" | "failed";
  validationStatus: "unknown" | "pending" | "passed" | "failed";
}

export interface ErrorItem extends ItemBase {
  type: "error";
  message: string;
  willRetry: boolean;
  errorInfo: Record<string, unknown> | null;
}

export type Item =
  | UserMessageItem
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
  | ErrorItem;

/** Soft hand-off hint payload from ``turn/metaSkill/hint``.
 *
 * The runtime emits this when ``match_meta_skill`` finds a strong
 * keyword overlap with one of the curated 能力包 / Meta-Skill yaml
 * packs. Frontend renders a dismissible chip pointing the user at
 * the catalog page; ReAct still runs so the user gets an answer
 * either way. ``dismissed`` is client-side only — the backend
 * doesn't track which hints were closed.
 */
export interface MetaSkillHint {
  name: string;
  description: string;
  kind: string;
  affinity: string[];
  stepCount: number;
  dismissed?: boolean;
}

/** One codebase source the agent grounded a turn on.
 *
 * ``kind: "doc"`` is a project-wiki page (``title`` is its heading);
 * ``kind: "source"`` is a source-file chunk (``path`` carries ``file:line``).
 * Emitted via ``turn/grounding`` when a code/project turn folds relevant wiki
 * pages + source chunks into the prompt. Faithful: exactly what was injected.
 */
export interface GroundingSource {
  kind: "doc" | "source";
  title: string;
  path: string;
}

/**
 * Defense in depth for historic turns produced before backend prompt-scope
 * isolation. Agent profile pages must never be shown as ordinary project
 * evidence under another agent's response.
 */
export function isPrivateAgentGroundingSource(
  source: GroundingSource,
): boolean {
  const path = source.path
    .replaceAll("\\", "/")
    .replace(/^\.\/?/, "")
    .toLowerCase();
  return path.startsWith("agents/") || path.startsWith("20-backend/26-agents/");
}

export interface Turn {
  id: string;
  threadId: string;
  status: TurnStatus;
  startedAt: string;
  completedAt: string | null;
  items: Item[];
  error: Record<string, unknown> | null;
  metaSkillHint?: MetaSkillHint;
  /** Project docs/chunks this turn was grounded on (``turn/grounding``).
   * The realtime adapter folds it onto the AI reply's
   * ``additional_kwargs.grounding`` so the chat renders a grounding chip. */
  grounding?: GroundingSource[];
  phases?: AgentPhaseSnapshot[];
  workspaceFocus?: WorkspaceFocus | null;
  workbenchSnapshot?: WorkbenchSnapshotV2 | null;
  /** Human-readable reason the turn was interrupted (null if not interrupted). */
  interruptReason?: string | null;
  objectiveId?: string | null;
  taskId?: string | null;
  checkpointId?: number | null;
  outcomeReason?: string | null;
  /** Canonical semantic completion result from the runtime. Unlike the
   * transport status, this distinguishes partial and warning deliveries. */
  completionDecision?: {
    outcome:
      | "completed"
      | "completed_with_warning"
      | "partial"
      | "blocked_on_user"
      | "paused"
      | "cancelled"
      | "failed";
    reason: string;
    success: boolean;
    terminal: boolean;
    resumable: boolean;
    retryable: boolean;
  } | null;
}

// Lightweight thread metadata for ``thread/list`` responses. Keeps the
// payload small so a sidebar with hundreds of threads doesn't pull
// every item into memory; the full ``Turn[]`` arrives only on
// ``thread/resume``.
export interface ThreadSummary {
  threadId: string;
  path: string;
  createdAt: string;
  updatedAt: string;
  turnCount: number;
  lastTurnStatus: TurnStatus | null;
  archived: boolean;
}

export interface Conversation {
  threadId: string;
  turns: Turn[];
  pendingApprovals: PendingApproval[];
  tokenUsage: Record<string, unknown> | null;
  resumeState: "needsResume" | "resuming" | "resumed";
  /**
   * True when thread/resume was paginated and turns older than
   * `turns[0]` exist on disk. Page backwards with the hook's
   * `loadOlderTurns()`.
   */
  hasMoreTurns: boolean;
  /**
   * Workflow (multi-agent orchestration) completion notifications pushed
   * over the realtime channel via `workflow/completed`. Kept bounded —
   * the UI shows the most recent one as a banner/chip.
   */
  workflowNotifications: WorkflowNotification[];
}

export interface WorkflowNotification {
  threadId: string;
  workflowName: string;
  workflowDescription: string;
  runId: string;
  stopReason: string;
  success: boolean;
  agentsStarted: number;
  error?: string | null;
  receivedAt: string;
}

export interface PendingApproval {
  requestId: string | number;
  method: string;
  params: Record<string, unknown>;
  createdAt: string;
}

export function emptyConversation(threadId: string): Conversation {
  return {
    threadId,
    turns: [],
    pendingApprovals: [],
    tokenUsage: null,
    resumeState: "needsResume",
    hasMoreTurns: false,
    workflowNotifications: [],
  };
}
