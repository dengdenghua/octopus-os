/**
 * Realtime-backed implementation of `useThreadStream`.
 *
 * It opens the realtime WebSocket, maps `Conversation` to the
 * `AgentThreadState` shape consumed by workspace pages, and exposes
 * live tool events derived from realtime items.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import {
  conversationIsLoading,
  conversationLastError,
  conversationStreamingMessage,
  conversationToAgentThreadState,
} from "@/core/threads/realtime-adapter";
import { swallow } from "@/core/utils/log";
import { toast } from "sonner";
import { useI18n } from "@/core/i18n/hooks";
import { useRealtimeThread, type StreamVitals } from "@/core/realtime";
import { itemStreamText } from "@/core/realtime/reducer";
import type {
  AgentPhaseSnapshot,
  ApprovalItem,
  ArtifactItem,
  CommandExecutionItem,
  Conversation,
  FileChangeItem,
  Item,
  McpToolCallItem,
  SubagentItem,
  PendingApproval,
  TodoListItem,
  Turn,
  VerificationItem,
  VisibilityItem,
  WorkbenchSnapshotV2,
  WorkspaceFocus,
} from "@/core/realtime/items";

import type { BaseStream } from "@/core/api/use-stream-types";
import type { LiveToolEvent } from "@/components/workspace/live-tool-timeline";
import type { AgentThreadState, ReasoningEffort } from "@/core/threads/types";
import type { ToolEndEvent } from "@/core/threads/hooks";
import { getStreamErrorMessage } from "@/core/threads/errors";
import {
  permissionRuntimeConfig,
  type ApprovalPolicy,
  type NetworkAccessMode,
  type SandboxPolicy,
} from "@/core/permissions";
import {
  promptInputFilePartToFile,
  uploadFiles,
  type PromptInputMessage,
  type UploadedFileInfo,
} from "@/core/uploads";
import {
  commandExecutionInput,
  commandExecutionToolName,
} from "./realtime-tool-compat";
import { liveEventIsReportLike } from "./report-deliverable";
import {
  applyCodexComposerModeContext,
  parseCodexComposerModeMarker,
} from "./codex-composer-mode";
import {
  RETRY_PENDING_MESSAGE_EVENT,
  acknowledgedClientMessageIds,
  mergeOptimisticHumanMessages,
  optimisticMessageReducer,
  type OptimisticMessageAction,
  type PendingOutboundMessage,
} from "./optimistic-messages";

/** File payload accepted by `sendMessage`. */
export interface FileInMessage {
  file_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
}

/** Full BaseStream shape consumed by workspace pages. */
type ExposedRealtimeThread = BaseStream<AgentThreadState>;

type SendMessageFn = (
  threadId: string,
  message: PromptInputMessage,
  ...args: unknown[]
) => void;

function newClientMessageId(): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}_${Math.random().toString(16).slice(2)}`;
  return `itm_user_${suffix}`;
}

export type UseThreadStreamRealtimeResult = readonly [
  ExposedRealtimeThread & { vitals: StreamVitals },
  SendMessageFn,
  boolean,
  LiveToolEvent[],
  LiveToolEvent[],
  {
    pendingApprovals: PendingApproval[];
    resolveApproval: (requestId: string | number, accept: boolean) => void;
    hasMoreTurns: boolean;
    loadOlderTurns: () => Promise<void>;
  },
];

export interface UseThreadStreamRealtimeOptions {
  threadId: string;
  /** Persisted "remember this model" picked by the page header. */
  model?: string;
  /** Default approval policy for new turns. Code page is permissive. */
  approvalPolicy?: ApprovalPolicy;
  /** Sandbox policy paired with the permission preset. */
  sandboxPolicy?: SandboxPolicy;
  /** Lifecycle callbacks derived from realtime conversation transitions. */
  onStart?: (threadId: string) => void;
  onFinish?: (state: AgentThreadState) => void;
  onToolEnd?: (event: ToolEndEvent) => void;
  /** Opaque settings bag surfaced to the server as turn context. */
  context?: unknown;
}

function reasoningEffortValue(value: unknown): ReasoningEffort | undefined {
  // `off` is a UI/provider capability value, not a valid public
  // turn/start effort.  Preserve it in metadata so the selected provider can
  // disable thinking, but never forward it as the protocol-level `effort`
  // field (which only accepts concrete reasoning tiers).
  if (value === "off") return undefined;
  if (
    value === "minimal" ||
    value === "low" ||
    value === "medium" ||
    value === "high" ||
    value === "xhigh" ||
    value === "max"
  ) {
    return value;
  }
  return undefined;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function topologyIdValue(value: Record<string, unknown>): string | undefined {
  return stringValue(value.topology_id) ?? stringValue(value.topologyId);
}

function stripUndefinedValues(
  value: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(value).filter(([, entry]) => entry !== undefined),
  );
}

function activeConversationThreadId(
  conv: Conversation,
  fallbackThreadId: string,
): string {
  const lastTurnThreadId = conv.turns[conv.turns.length - 1]?.threadId;
  if (lastTurnThreadId && lastTurnThreadId !== "new") {
    return lastTurnThreadId;
  }
  if (conv.threadId && conv.threadId !== "new") {
    return conv.threadId;
  }
  return fallbackThreadId;
}

function toMillis(value: string | null | undefined): number {
  return toOptionalMillis(value) ?? Date.now();
}

function toOptionalMillis(
  value: string | null | undefined,
): number | undefined {
  if (!value) return undefined;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function liveStatus(status: Item["status"]): LiveToolEvent["status"] {
  if (status === "inProgress") return "running";
  if (status === "completed" || status === "interrupted") return "done";
  return "error";
}

function finishFields(
  status: LiveToolEvent["status"],
  startedAt: number,
  turn: Turn,
  durationMs?: number | null,
): Pick<LiveToolEvent, "finishedAt" | "durationMs"> {
  if (status === "running" || status === "waiting_approval") return {};
  const finishedAt =
    durationMs != null && durationMs >= 0
      ? startedAt + durationMs
      : (toOptionalMillis(turn.completedAt) ?? startedAt);
  return {
    finishedAt,
    durationMs:
      durationMs != null && durationMs >= 0
        ? durationMs
        : Math.max(0, finishedAt - startedAt),
  };
}

function commandItemToLiveEvent(
  item: CommandExecutionItem,
  turn: Turn,
  iteration: number,
): LiveToolEvent {
  const startedAt = toMillis(item.createdAt);
  const status = liveStatus(item.status);
  return {
    id: item.id,
    name: commandExecutionToolName(item),
    status,
    startedAt,
    iteration,
    input: commandExecutionInput(item),
    output: itemStreamText(item) || undefined,
    ...finishFields(status, startedAt, turn),
  };
}

function requestedAgentIdFromPrompt(value: unknown): string | undefined {
  const prompt = stringValue(value);
  if (!prompt) return undefined;
  return stringValue(prompt.match(/^#\s*Role:\s*([^\s]+)\s*$/im)?.[1]);
}

/** Stable public lane id for one child.
 *
 * The backend can resolve many custom ids to one builtin role (for example
 * reader_readme + reader_pyproject -> explorer). Prefer the requested id;
 * older histories did not persist it, so recover it from the wrapped prompt
 * or fall back to the unique codename when agent_id is merely the shared role.
 */
function canonicalSubagentId(
  args: Record<string, unknown>,
  options: {
    role?: string;
    codename?: string;
    prompt?: unknown;
  } = {},
): string | undefined {
  const requested =
    stringValue(args.requested_agent_id) ??
    stringValue(args.requestedAgentId) ??
    requestedAgentIdFromPrompt(options.prompt);
  if (requested) return requested;

  const raw = stringValue(args.agent_id) ?? stringValue(args.agentId);
  if (raw && raw !== options.role) return raw;
  return options.codename ?? raw ?? options.role;
}

function mcpItemToLiveEvent(
  item: McpToolCallItem,
  turn: Turn,
  iteration: number,
): LiveToolEvent {
  const startedAt = toMillis(item.createdAt);
  const status = liveStatus(item.status);
  // Sub-agent lifecycle markers: the bridge writes synthesised
  // ``mcpToolCall`` items whose ``tool`` field is one of the magic
  // marker strings (mirrors ``runtime.protocol.items.ItemMarker``).
  // Translate them into ``lifecycle`` events so the AgentWorkbench
  // panel can render a tile from the spawn moment instead of waiting
  // for the first ``sub_tool_*`` event.
  if (item.tool === "__subagent_spawned__") {
    const args = (item.arguments ?? {}) as Record<string, unknown>;
    const codename =
      typeof args.codename === "string" ? args.codename : undefined;
    const avatar = typeof args.avatar === "string" ? args.avatar : undefined;
    const role = typeof args.role === "string" ? args.role : undefined;
    const roleDisplayName =
      typeof args.role_display_name === "string"
        ? args.role_display_name
        : undefined;
    const roleDescription =
      typeof args.role_description === "string"
        ? args.role_description
        : undefined;
    const agentId = canonicalSubagentId(args, {
      role,
      codename,
      prompt: args.prompt_preview,
    });
    const parentToolUseId =
      typeof args.parent_tool_use_id === "string"
        ? args.parent_tool_use_id
        : typeof args.parentToolUseId === "string"
          ? args.parentToolUseId
          : undefined;
    return {
      id: item.id,
      name: "subagent",
      status: "running",
      lifecycle: "spawned",
      startedAt,
      iteration,
      agentId,
      subAgentRole: role,
      subagentCodename: codename,
      subagentAvatar: avatar,
      subagentRoleDisplayName: roleDisplayName,
      subagentRoleDescription: roleDescription,
      parentToolUseId,
      input: { ...args },
    };
  }
  if (item.tool === "__subagent_finished__") {
    const args = (item.arguments ?? {}) as Record<string, unknown>;
    const result = (item.result ?? {}) as Record<string, unknown>;
    const codename =
      typeof result.codename === "string" ? result.codename : undefined;
    const avatar =
      typeof result.avatar === "string" ? result.avatar : undefined;
    const role = typeof result.role === "string" ? result.role : undefined;
    const agentId = canonicalSubagentId(result, { role, codename });
    const parentToolUseId =
      typeof result.parent_tool_use_id === "string"
        ? result.parent_tool_use_id
        : typeof result.parentToolUseId === "string"
          ? result.parentToolUseId
          : typeof args.parent_tool_use_id === "string"
            ? args.parent_tool_use_id
            : typeof args.parentToolUseId === "string"
              ? args.parentToolUseId
              : undefined;
    const ok = result.ok !== false;
    const iterationCount =
      typeof result.iteration_count === "number"
        ? result.iteration_count
        : undefined;
    const filesTouched = Array.isArray(result.files_touched)
      ? result.files_touched.filter((p): p is string => typeof p === "string")
      : undefined;
    const durationS =
      typeof result.duration_s === "number" ? result.duration_s : undefined;
    const durationMs =
      durationS !== undefined
        ? Math.max(0, Math.round(durationS * 1000))
        : undefined;
    const finishedAt =
      durationMs !== undefined ? startedAt + durationMs : startedAt;
    // The backend has always sent a concrete cause here (an SSL disconnect, a
    // round cap, a routing refusal), but this mapper whitelists fields by name
    // and `error` was not on the list -- so a failed lane reached the UI as a
    // red tint and nothing else. Users could only ask "why did it fail".
    const errorText =
      typeof result.error === "string" && result.error.trim()
        ? result.error.trim()
        : undefined;
    // The bridge now carries the sub-agent's answer text on ``result.output``
    // (it previously shipped only metadata). Surface it as ``observation`` so
    // the workbench's sub-agent view can render a readable final message
    // instead of falling back to the raw result envelope. Older snapshots
    // without it simply leave ``observation`` unset.
    const answerText =
      typeof result.output === "string" && result.output.trim()
        ? result.output.trim()
        : typeof result.summary === "string" && result.summary.trim()
          ? result.summary.trim()
          : undefined;
    return {
      id: item.id,
      name: "subagent",
      status: ok ? "done" : "error",
      error: errorText,
      lifecycle: "finished",
      startedAt,
      finishedAt,
      durationMs,
      iteration,
      agentId,
      subAgentRole: role,
      subagentCodename: codename,
      subagentAvatar: avatar,
      parentToolUseId,
      iterationCount,
      filesTouched,
      observation: answerText,
      input: { ...result },
      output: result,
    };
  }
  if (item.tool === "__subagent_progress__") {
    const args = (item.arguments ?? {}) as Record<string, unknown>;
    const preview = item.progress?.preview;
    return {
      id: item.id,
      name: "subagent_progress",
      status,
      startedAt,
      iteration,
      agentId: canonicalSubagentId(args, {
        role:
          typeof args.sub_agent_role === "string"
            ? args.sub_agent_role
            : undefined,
        codename:
          typeof args.subagent_codename === "string"
            ? args.subagent_codename
            : undefined,
      }),
      subAgentRole:
        typeof args.sub_agent_role === "string"
          ? args.sub_agent_role
          : undefined,
      subagentCodename:
        typeof args.subagent_codename === "string"
          ? args.subagent_codename
          : undefined,
      subagentAvatar:
        typeof args.subagent_avatar === "string"
          ? args.subagent_avatar
          : undefined,
      parentToolUseId:
        typeof args.parent_tool_use_id === "string"
          ? args.parent_tool_use_id
          : undefined,
      input: { ...args, progress: item.progress ?? null },
      output: preview,
      observation:
        typeof preview === "string" && preview.trim()
          ? preview.trim()
          : undefined,
      ...finishFields(status, startedAt, turn, item.durationMs),
    };
  }
  const args = (item.arguments ?? {}) as Record<string, unknown>;
  const isSubagentTool = item.server === "subagent";
  const childRole =
    typeof args.sub_agent_role === "string"
      ? args.sub_agent_role
      : typeof args.role === "string"
        ? args.role
        : undefined;
  const childCodename =
    typeof args.subagent_codename === "string"
      ? args.subagent_codename
      : typeof args.codename === "string"
        ? args.codename
        : undefined;
  const childAgentId = canonicalSubagentId(args, {
    role: childRole,
    codename: childCodename,
  });
  const childAvatar =
    typeof args.subagent_avatar === "string"
      ? args.subagent_avatar
      : typeof args.avatar === "string"
        ? args.avatar
        : undefined;
  const childParentId =
    typeof args.parent_tool_use_id === "string"
      ? args.parent_tool_use_id
      : typeof args.parentToolUseId === "string"
        ? args.parentToolUseId
        : undefined;
  const toolName =
    item.server === "team" && item.tool === "team_swarm"
      ? "team_swarm"
      : item.tool
        ? `mcp:${item.tool}`
        : "mcp";
  return {
    id: item.id,
    name: isSubagentTool && item.tool ? item.tool : toolName,
    status,
    startedAt,
    iteration,
    agentId: childAgentId,
    subAgentRole: childRole,
    subagentCodename: childCodename,
    subagentAvatar: childAvatar,
    parentToolUseId: childParentId,
    input: {
      server: item.server,
      tool: item.tool,
      arguments: args,
      progress: item.progress ?? null,
    },
    output: item.error
      ? item.progress
        ? { error: item.error, progress: item.progress }
        : { error: item.error }
      : item.progress
        ? { result: item.result, progress: item.progress }
        : item.result,
    ...finishFields(status, startedAt, turn, item.durationMs),
  };
}

function fileChangeItemToLiveEvent(
  item: FileChangeItem,
  turn: Turn,
  iteration: number,
): LiveToolEvent {
  const startedAt = toMillis(item.createdAt);
  const status = liveStatus(item.status);
  return {
    id: item.id,
    name: "file_change",
    status,
    startedAt,
    iteration,
    input: {
      changes: item.changes,
      grantRoot: item.grantRoot,
    },
    output: item.changes,
    ...finishFields(status, startedAt, turn),
  };
}

function todoItemToLiveEvent(
  item: TodoListItem,
  turn: Turn,
  iteration: number,
): LiveToolEvent {
  const startedAt = toMillis(item.createdAt);
  const status = liveStatus(item.status);
  return {
    id: item.id,
    name: "todo_write",
    status,
    startedAt,
    iteration,
    input: {
      items: item.plan.map((entry) => ({
        content: entry.title,
        status: entry.status,
      })),
      explanation: item.explanation,
    },
    output: item.plan,
    ...finishFields(status, startedAt, turn),
  };
}

function phaseStatusToTodoStatus(status: AgentPhaseSnapshot["status"]): string {
  if (status === "done") return "completed";
  if (status === "running" || status === "waiting_approval") {
    return "in_progress";
  }
  if (status === "error") return "error";
  return "pending";
}

function phaseSnapshotEventStatus(turn: Turn): LiveToolEvent["status"] {
  const phases = turn.phases ?? [];
  if (phases.some((phase) => phase.status === "error")) return "error";
  if (phases.some((phase) => phase.status === "waiting_approval")) {
    return "waiting_approval";
  }
  if (phases.some((phase) => phase.status === "running")) return "running";
  if (phases.length > 0 && phases.every((phase) => phase.status === "done")) {
    return "done";
  }
  return turn.status === "inProgress" ? "running" : "done";
}

function phaseSnapshotsToLiveEvent(
  turn: Turn,
  iteration: number,
): LiveToolEvent | null {
  const phases = turn.phases ?? [];
  if (phases.length === 0) return null;
  const startedAt = toMillis(turn.startedAt);
  const status = phaseSnapshotEventStatus(turn);
  return {
    id: `server-phases:${turn.id}`,
    name: "todo_write",
    status,
    startedAt,
    iteration,
    input: {
      items: phases.map((phase) => ({
        content: phase.title,
        activeForm: phase.title,
        status: phaseStatusToTodoStatus(phase.status),
        phaseId: phase.id,
        index: phase.index,
        total: phase.total,
        activeItemId: phase.activeItemId,
        phaseKind: phase.phaseKind ?? null,
      })),
      workspaceFocus: turn.workspaceFocus,
      workbenchSnapshot: turn.workbenchSnapshot ?? null,
      source: "turn.phases",
    },
    output: phases,
    ...finishFields(status, startedAt, turn),
  };
}

function subagentItemToLiveEvent(
  item: SubagentItem,
  turn: Turn,
  iteration: number,
): LiveToolEvent {
  const startedAt = toMillis(item.createdAt);
  const status = liveStatus(item.status);
  return {
    id: item.id,
    name: "subagent",
    status,
    startedAt,
    iteration,
    agentId: item.subagentId,
    subAgentRole: item.role ?? undefined,
    subagentCodename: item.codename ?? undefined,
    subagentAvatar: item.avatar ?? undefined,
    parentToolUseId: item.parentItemId ?? undefined,
    input: {
      subagentId: item.subagentId,
      role: item.role,
      name: item.name,
      parentItemId: item.parentItemId,
    },
    output: item.error ? { error: item.error } : item.summary,
    iterationCount: item.iterationCount ?? undefined,
    filesTouched: item.filesTouched,
    ...finishFields(status, startedAt, turn),
  };
}

function approvalItemToLiveEvent(
  item: ApprovalItem,
  turn: Turn,
  iteration: number,
): LiveToolEvent {
  const startedAt = toMillis(item.createdAt);
  const status: LiveToolEvent["status"] =
    item.decision === "pending" ? "waiting_approval" : liveStatus(item.status);
  return {
    id: item.targetItemId ?? item.id,
    name: item.method || "approval",
    status,
    startedAt,
    iteration,
    input: {
      requestId: item.requestId,
      method: item.method,
      params: item.params,
      decision: item.decision,
    },
    ...finishFields(status, startedAt, turn),
  };
}

function verificationItemToLiveEvent(
  item: VerificationItem,
  turn: Turn,
  iteration: number,
): LiveToolEvent {
  const startedAt = toMillis(item.createdAt);
  const status = liveStatus(item.status);
  return {
    id: item.id,
    name: `verification:${item.kind}`,
    status,
    startedAt,
    iteration,
    input: {
      command: item.command,
      relatedFiles: item.relatedFiles,
      relatedChangeItemIds: item.relatedChangeItemIds,
    },
    output: {
      exitCode: item.exitCode,
      summary: item.summary,
      stdoutTail: item.stdoutTail,
      stderrTail: item.stderrTail,
    },
    ...finishFields(status, startedAt, turn),
  };
}

function visibilityItemToLiveEvent(
  item: VisibilityItem,
  turn: Turn,
  iteration: number,
): LiveToolEvent {
  const startedAt = toMillis(item.createdAt);
  const status = liveStatus(item.status);
  return {
    id: item.id,
    name: "visibility",
    status,
    startedAt,
    iteration,
    input: {
      summary: item.summary,
      steps: item.steps,
    },
    ...finishFields(status, startedAt, turn),
  };
}

function artifactItemToLiveEvent(
  item: ArtifactItem,
  turn: Turn,
  iteration: number,
): LiveToolEvent {
  const startedAt = toMillis(item.createdAt);
  const status = liveStatus(item.status);
  return {
    id: item.id,
    name: "artifact",
    status,
    startedAt,
    iteration,
    input: {
      artifactId: item.artifactId,
      kind: item.kind,
      path: item.path,
      title: item.title,
      workspaceFocus: {
        itemId: item.id,
        view: item.kind === "image" ? "image" : "artifact",
        title: item.title ?? item.path,
        subtitle: item.path,
        previewUrl: item.previewUrl,
      },
    },
    output: {
      previewUrl: item.previewUrl,
      renderStatus: item.renderStatus,
      validationStatus: item.validationStatus,
    },
    ...finishFields(status, startedAt, turn),
  };
}

function itemToLiveEvent(
  item: Item,
  turn: Turn,
  iteration: number,
): LiveToolEvent | null {
  if (item.type === "commandExecution") {
    return commandItemToLiveEvent(item, turn, iteration);
  }
  if (item.type === "mcpToolCall") {
    return mcpItemToLiveEvent(item, turn, iteration);
  }
  if (item.type === "fileChange") {
    return fileChangeItemToLiveEvent(item, turn, iteration);
  }
  if (item.type === "todo-list") {
    return todoItemToLiveEvent(item, turn, iteration);
  }
  if (item.type === "subagent") {
    return subagentItemToLiveEvent(item, turn, iteration);
  }
  if (item.type === "approval") {
    return approvalItemToLiveEvent(item, turn, iteration);
  }
  if (item.type === "verification") {
    return verificationItemToLiveEvent(item, turn, iteration);
  }
  if (item.type === "visibility") {
    return visibilityItemToLiveEvent(item, turn, iteration);
  }
  if (item.type === "artifact") {
    return artifactItemToLiveEvent(item, turn, iteration);
  }
  return null;
}

// LiveToolEvent identity caches. The realtime reducer rebuilds only the
// turn/item a delta touched, so an unchanged ``Item`` reference implies
// unchanged event content except for the inputs that come from outside
// the item: the owning turn's ``completedAt`` (feeds finishFields) and
// the positional ``iteration``. Cache per item and validate those two so
// unchanged items yield reference-equal events across calls (downstream
// memo/snapshot layers key on identity). Conversation-scope and
// last-turn-scope caches are separate because the same item receives a
// different ``iteration`` in each. WeakMaps: entries die with the
// reducer state that owns the keys.
interface CachedItemEvent {
  event: LiveToolEvent;
  turnCompletedAt: Turn["completedAt"] | undefined;
  iteration: number;
  turnIndex: number;
}
interface CachedPhaseEvent {
  event: LiveToolEvent;
  turnId: string;
  turnStatus: Turn["status"];
  startedAt: string;
  completedAt: Turn["completedAt"] | undefined;
  workspaceFocus: WorkspaceFocus | null | undefined;
  workbenchSnapshot: WorkbenchSnapshotV2 | null | undefined;
  iteration: number;
  turnIndex: number;
}
interface CachedApprovalEvent {
  event: LiveToolEvent;
  iteration: number;
}
interface LiveEventScopeCache {
  items: WeakMap<Item, CachedItemEvent>;
  phases: WeakMap<AgentPhaseSnapshot[], CachedPhaseEvent>;
  approvals: WeakMap<PendingApproval, CachedApprovalEvent>;
}
const newLiveEventScopeCache = (): LiveEventScopeCache => ({
  items: new WeakMap(),
  phases: new WeakMap(),
  approvals: new WeakMap(),
});
const conversationEventCache = newLiveEventScopeCache();
const lastTurnEventCache = newLiveEventScopeCache();

// Scope-level array caches. The reducer only allocates a new ``turns`` array (and
// a new last-turn object) when a turn was added/replaced/streamed; a stable
// reference therefore implies the derived event array is unchanged. When the
// owning scope reference AND the ``pendingApprovals`` reference are both stable,
// the previously-computed ``LiveToolEvent[]`` array is returned as-is, so its
// identity is stable across calls and downstream memo/snapshot layers keyed on
// identity hold during streaming. WeakMaps: entries die with the reducer state.
interface CachedEventScope {
  pendingApprovals: PendingApproval[];
  events: LiveToolEvent[];
}
interface CachedTurnEventScope {
  turnIndex: number;
  events: LiveToolEvent[];
}
const conversationEventScopeCache = new WeakMap<Turn[], CachedEventScope>();
const conversationTurnEventScopeCache = new WeakMap<
  Turn,
  CachedTurnEventScope
>();
const lastTurnEventScopeCache = new WeakMap<Turn, CachedEventScope>();
const lastTurnEmptyScopeCache = new WeakMap<Turn[], CachedEventScope>();

// Stamp the report-deliverable flag on every freshly constructed event
// (cache-miss paths only). The page-level "requires report deliverable"
// check reads the flag instead of stringifying input/output on every
// render frame; the WeakMap caches above bound the stringify cost to
// once per changed item.
function withReportLikeFlag(event: LiveToolEvent): LiveToolEvent {
  event.isReportLike = liveEventIsReportLike(event);
  return event;
}

function cachedItemToLiveEvent(
  cache: LiveEventScopeCache,
  item: Item,
  turn: Turn,
  iteration: number,
  turnIndex: number,
): LiveToolEvent | null {
  const hit = cache.items.get(item);
  if (
    hit &&
    hit.turnCompletedAt === turn.completedAt &&
    hit.iteration === iteration &&
    hit.turnIndex === turnIndex
  ) {
    return hit.event;
  }
  const event = itemToLiveEvent(item, turn, iteration);
  if (event) {
    event.turnId = turn.id;
    event.turnIndex = turnIndex;
    cache.items.set(item, {
      event: withReportLikeFlag(event),
      turnCompletedAt: turn.completedAt,
      iteration,
      turnIndex,
    });
  }
  return event;
}

// Keyed by the phases array (stable across item deltas; replaced by the
// reducer on turn/plan/updated) because the turn object itself gets a
// new identity on every delta while streaming.
function cachedPhaseSnapshotsToLiveEvent(
  cache: LiveEventScopeCache,
  turn: Turn,
  iteration: number,
  turnIndex: number,
): LiveToolEvent | null {
  const phases = turn.phases;
  if (!phases || phases.length === 0) return null;
  const hit = cache.phases.get(phases);
  if (
    hit &&
    hit.turnId === turn.id &&
    hit.turnStatus === turn.status &&
    hit.startedAt === turn.startedAt &&
    hit.completedAt === turn.completedAt &&
    hit.workspaceFocus === turn.workspaceFocus &&
    hit.workbenchSnapshot === turn.workbenchSnapshot &&
    hit.iteration === iteration &&
    hit.turnIndex === turnIndex
  ) {
    return hit.event;
  }
  const event = phaseSnapshotsToLiveEvent(turn, iteration);
  if (event) {
    event.turnId = turn.id;
    event.turnIndex = turnIndex;
    cache.phases.set(phases, {
      event: withReportLikeFlag(event),
      turnId: turn.id,
      turnStatus: turn.status,
      startedAt: turn.startedAt,
      completedAt: turn.completedAt,
      workspaceFocus: turn.workspaceFocus,
      workbenchSnapshot: turn.workbenchSnapshot,
      iteration,
      turnIndex,
    });
  }
  return event;
}

function cachedApprovalToLiveEvent(
  cache: LiveEventScopeCache,
  approval: PendingApproval,
  iteration: number,
): LiveToolEvent {
  const hit = cache.approvals.get(approval);
  if (hit && hit.iteration === iteration) return hit.event;
  const event = withReportLikeFlag(approvalToLiveEvent(approval, iteration));
  cache.approvals.set(approval, { event, iteration });
  return event;
}

/** Normalize aliases within one turn after all items have been mapped.
 *
 * Legacy lifecycle snapshots only persisted the requested custom id on the
 * spawn prompt; finish/tool rows used the generated codename. The spawn mapper
 * recovers the custom id, then this pass rewrites sibling rows to that same id
 * so every consumer (dock, workbench filtering, process trace) sees one lane.
 */
function normalizeTurnSubagentAliases(
  events: LiveToolEvent[],
): LiveToolEvent[] {
  const aliases = new Map<string, string>();
  for (const event of events) {
    if (
      event.lifecycle === "spawned" &&
      event.agentId &&
      event.agentId !== "__main__" &&
      event.subagentCodename &&
      event.subagentCodename !== event.agentId
    ) {
      aliases.set(event.subagentCodename, event.agentId);
    }
  }
  if (aliases.size === 0) return events;
  return events.map((event) => {
    const canonical = event.agentId ? aliases.get(event.agentId) : undefined;
    return canonical ? { ...event, agentId: canonical } : event;
  });
}

function liveToolEventsFromTurn(
  turn: Turn,
  turnIndex: number,
): LiveToolEvent[] {
  const cached = conversationTurnEventScopeCache.get(turn);
  if (cached?.turnIndex === turnIndex) return cached.events;
  const rawEvents = turn.items
    .map((item, itemIndex) =>
      cachedItemToLiveEvent(
        conversationEventCache,
        item,
        turn,
        turnIndex + itemIndex + 1,
        turnIndex,
      ),
    )
    .filter((event): event is LiveToolEvent => event !== null);
  const itemEvents = normalizeTurnSubagentAliases(rawEvents);
  const phaseEvent = cachedPhaseSnapshotsToLiveEvent(
    conversationEventCache,
    turn,
    turnIndex + turn.items.length + 1,
    turnIndex,
  );
  const events = phaseEvent ? [...itemEvents, phaseEvent] : itemEvents;
  conversationTurnEventScopeCache.set(turn, { turnIndex, events });
  return events;
}

export function liveToolEventsFromConversation(
  conv: Conversation,
): LiveToolEvent[] {
  const cached = conversationEventScopeCache.get(conv.turns);
  if (cached && cached.pendingApprovals === conv.pendingApprovals) {
    return cached.events;
  }
  const itemEvents = conv.turns.flatMap(liveToolEventsFromTurn);
  const events = [
    ...itemEvents,
    ...conv.pendingApprovals.map((approval, index) =>
      cachedApprovalToLiveEvent(
        conversationEventCache,
        approval,
        conv.turns.length + index + 1,
      ),
    ),
  ];
  conversationEventScopeCache.set(conv.turns, {
    pendingApprovals: conv.pendingApprovals,
    events,
  });
  return events;
}

export function liveToolEventsFromLastTurn(
  conv: Conversation,
): LiveToolEvent[] {
  const last = conv.turns[conv.turns.length - 1];
  if (!last) {
    const cached = lastTurnEmptyScopeCache.get(conv.turns);
    if (cached && cached.pendingApprovals === conv.pendingApprovals) {
      return cached.events;
    }
    const events = conv.pendingApprovals.map((approval, index) =>
      cachedApprovalToLiveEvent(lastTurnEventCache, approval, index + 1),
    );
    lastTurnEmptyScopeCache.set(conv.turns, {
      pendingApprovals: conv.pendingApprovals,
      events,
    });
    return events;
  }
  const cached = lastTurnEventScopeCache.get(last);
  if (cached && cached.pendingApprovals === conv.pendingApprovals) {
    return cached.events;
  }
  const rawItemEvents = last.items
    .map((item, index) =>
      cachedItemToLiveEvent(
        lastTurnEventCache,
        item,
        last,
        index + 1,
        conv.turns.length - 1,
      ),
    )
    .filter((event): event is LiveToolEvent => event !== null);
  const itemEvents = normalizeTurnSubagentAliases(rawItemEvents);
  const phaseEvent = cachedPhaseSnapshotsToLiveEvent(
    lastTurnEventCache,
    last,
    itemEvents.length + 1,
    conv.turns.length - 1,
  );
  const eventsWithPhase = phaseEvent ? [...itemEvents, phaseEvent] : itemEvents;
  const events = [
    ...eventsWithPhase,
    ...conv.pendingApprovals.map((approval, index) =>
      cachedApprovalToLiveEvent(
        lastTurnEventCache,
        approval,
        eventsWithPhase.length + index + 1,
      ),
    ),
  ];
  lastTurnEventScopeCache.set(last, {
    pendingApprovals: conv.pendingApprovals,
    events,
  });
  return events;
}

function approvalToLiveEvent(
  approval: PendingApproval,
  iteration: number,
): LiveToolEvent {
  const params = approval.params as {
    itemId?: unknown;
    tool?: unknown;
    argsPreview?: unknown;
    detail?: unknown;
  };
  const tool =
    typeof params.tool === "string" && params.tool
      ? params.tool
      : "tool_approval";
  return {
    id: String(params.itemId || approval.requestId),
    name: tool,
    status: "waiting_approval",
    startedAt: toMillis(approval.createdAt),
    iteration,
    input: {
      tool,
      argsPreview: params.argsPreview,
      detail: params.detail,
      requestId: approval.requestId,
    },
  };
}

export function useThreadStreamRealtime(
  opts: UseThreadStreamRealtimeOptions,
): UseThreadStreamRealtimeResult {
  const {
    threadId,
    model,
    approvalPolicy,
    sandboxPolicy,
    onStart,
    onFinish,
    onToolEnd,
    context,
  } = opts;

  const realtime = useRealtimeThread({ threadId });
  const {
    state,
    connected,
    startTurn,
    steer,
    interrupt,
    resume,
    compact,
    resolveApproval,
    loadOlderTurns,
    vitals,
  } = realtime;
  const [isUploading, setIsUploading] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [pendingOutboundMessages, dispatchOptimisticMessage] = useReducer(
    optimisticMessageReducer,
    [],
  );
  const pendingOutboundRef = useRef<PendingOutboundMessage[]>([]);
  pendingOutboundRef.current = pendingOutboundMessages;
  const updateOptimisticMessages = useCallback(
    (action: OptimisticMessageAction) => {
      // Keep an eager mirror so two submits in the same render frame cannot
      // both believe they are the first unacknowledged turn/start.
      pendingOutboundRef.current = optimisticMessageReducer(
        pendingOutboundRef.current,
        action,
      );
      dispatchOptimisticMessage(action);
    },
    [],
  );
  const { t } = useI18n();

  // A thread change (e.g. starting a new conversation) must purge any stale
  // send-failure from the previous thread. `useRealtimeThread` resets its own
  // state (including `realtimeError`) when `threadId` changes, but `sendError`
  // lives here and would otherwise keep the red failure banner alive in the
  // fresh workspace until the next send.
  useEffect(() => {
    setSendError(null);
    updateOptimisticMessages({ type: "reset" });
  }, [threadId, updateOptimisticMessages]);

  // A transport failure belongs to the broken connection, not the recovered
  // session. Clear its banner only after both layers are authoritative again:
  // the socket is open and thread/resume has settled successfully. In
  // particular, keep the error visible for needsResume (including an
  // unknown/inaccessible historical thread) so reconnecting the socket alone
  // cannot make a still-unsendable conversation look healthy.
  useEffect(() => {
    if (connected && state.resumeState === "resumed") {
      setSendError(null);
    }
  }, [connected, state.resumeState]);

  const transportReady = connected && state.resumeState === "resumed";
  useEffect(() => {
    // A disconnect can demote an in-flight optimistic row to queued. Becoming
    // ready does not itself mean the row was sent; the delivery effect below
    // promotes it only when startTurn is actually invoked.
    if (!transportReady) {
      updateOptimisticMessages({ type: "transport-ready", ready: false });
    }
  }, [transportReady, updateOptimisticMessages]);
  const permissionRuntime = useMemo(
    () =>
      permissionRuntimeConfig(
        (context as { permission_mode?: unknown } | null | undefined)
          ?.permission_mode,
        // Explicit user opt-in only; when unset we pass undefined so the
        // mode's safe default (deny, or allow for full access) applies.
        (context as { network_access?: NetworkAccessMode } | null | undefined)
          ?.network_access,
      ),
    [context],
  );
  const effectiveApprovalPolicy =
    approvalPolicy ?? permissionRuntime.approvalPolicy;
  const effectiveSandboxPolicy =
    sandboxPolicy ?? permissionRuntime.sandboxPolicy;

  // Mapped conversation view. Re-derives only when
  // the underlying `state` object identity changes; the realtime
  // reducer already short-circuits when nothing changed, so this is
  // cheap.
  const mapped = useMemo<AgentThreadState>(
    () => conversationToAgentThreadState(state),
    [state],
  );
  const acknowledgedOutboundIds = useMemo(
    () => acknowledgedClientMessageIds(mapped.messages),
    [mapped.messages],
  );
  useEffect(() => {
    updateOptimisticMessages({
      type: "acknowledge",
      clientMessageIds: acknowledgedOutboundIds,
    });
  }, [acknowledgedOutboundIds, updateOptimisticMessages]);
  const visibleMessages = useMemo(
    () =>
      mergeOptimisticHumanMessages(mapped.messages, pendingOutboundMessages),
    [mapped.messages, pendingOutboundMessages],
  );
  const liveToolEvents = useMemo(
    () => liveToolEventsFromConversation(state),
    [state],
  );
  const lastTurnToolEvents = useMemo(
    () => liveToolEventsFromLastTurn(state),
    [state],
  );
  const approvalControls = useMemo(
    () => ({
      pendingApprovals: state.pendingApprovals,
      resolveApproval,
      // Backwards pagination — thread/resume returns the newest window
      // for large threads; older history pages in on demand.
      hasMoreTurns: state.hasMoreTurns,
      loadOlderTurns,
    }),
    [
      state.pendingApprovals,
      resolveApproval,
      state.hasMoreTurns,
      loadOlderTurns,
    ],
  );

  const isLoading = useMemo(() => conversationIsLoading(state), [state]);
  // The optimistic human bubble exists before turn/started is received. Treat
  // an outbound start that is actively being delivered as UI loading so the
  // assistant activity lane and composer state appear immediately after Send.
  // Keep lifecycle callbacks bound to the server-authoritative isLoading
  // below; this flag is presentation-only and never claims a turn started.
  const isAwaitingTurnStart = pendingOutboundMessages.some(
    (message) =>
      message.intent === "start" && message.deliveryState === "sending",
  );
  const isUiLoading = isLoading || isAwaitingTurnStart;
  const runningTurnId = isLoading
    ? state.turns[state.turns.length - 1]?.id
    : undefined;
  const realtimeError = useMemo(() => conversationLastError(state), [state]);
  // Send failures are tracked as strings; wrap them so the exposed
  // BaseStream.error honours its `Error | undefined` type. Consumers
  // (message-list) key network-error detection off `.message`, which
  // is unchanged by the wrapping. Memoised so `exposedThread` keeps a
  // stable identity across unrelated re-renders.
  const error = useMemo(
    () => (sendError ? new Error(sendError) : realtimeError),
    [sendError, realtimeError],
  );
  const streamingMessage = useMemo(
    () => conversationStreamingMessage(state),
    [state],
  );
  const isThreadLoading =
    state.resumeState === "resuming" && visibleMessages.length === 0;
  const activeThreadId = useMemo(
    () => activeConversationThreadId(state, threadId),
    [state, threadId],
  );

  // Lifecycle callbacks (onStart / onFinish / onToolEnd).
  const wasLoadingRef = useRef(false);
  const seenToolIdsRef = useRef<Set<string>>(new Set());
  const callbacksRef = useRef({ onStart, onFinish, onToolEnd });
  useEffect(() => {
    callbacksRef.current = { onStart, onFinish, onToolEnd };
  }, [onStart, onFinish, onToolEnd]);

  useEffect(() => {
    // Edge: idle -> busy -> call onStart with the active thread id.
    if (!wasLoadingRef.current && isLoading) {
      // A live turn supersedes any stale send-failure banner (the flag
      // is otherwise only cleared by the next manual send): the turn
      // either belongs to a delivered-after-all send or to a newer
      // attempt, so keeping the old error up would be misleading.
      setSendError(null);
      try {
        callbacksRef.current.onStart?.(activeThreadId || "");
      } catch (e) {
        swallow(e);
      }
    }
    // Edge: busy -> idle -> call onFinish with the final state.
    if (wasLoadingRef.current && !isLoading) {
      try {
        callbacksRef.current.onFinish?.(mapped);
      } catch (e) {
        swallow(e);
      }
    }
    wasLoadingRef.current = isLoading;
  }, [isLoading, activeThreadId, mapped]);

  useEffect(() => {
    // Walk all items; when we see a just-completed tool we haven't
    // surfaced yet, fire onToolEnd. Cheap because the reducer keeps
    // object identity stable for items that didn't change.
    const cb = callbacksRef.current.onToolEnd;
    if (!cb) return;
    for (const turn of state.turns) {
      for (const item of turn.items) {
        if (
          (item.type === "commandExecution" || item.type === "mcpToolCall") &&
          item.status !== "inProgress" &&
          !seenToolIdsRef.current.has(item.id)
        ) {
          seenToolIdsRef.current.add(item.id);
          try {
            cb({
              name:
                item.type === "commandExecution"
                  ? commandExecutionToolName(item)
                  : "mcp",
              data: item,
            });
          } catch (e) {
            swallow(e);
          }
        }
      }
    }
  }, [state]);

  // Stable `stop` ref so callers can `useRef(thread.stop)` without
  // tearing on every render (the code page does this around L1195).
  const stopRef = useRef(() => {
    void interrupt();
  });
  stopRef.current = () => {
    void interrupt();
  };
  const stop = useCallback(() => stopRef.current(), []);

  const refresh = useCallback(() => resume(), [resume]);

  const exposedThread = useMemo(
    () =>
      ({
        messages: visibleMessages,
        streamingMessage,
        subgraphStreams: {},
        values: mapped,
        isLoading: isUiLoading,
        isThreadLoading,
        error,
        stop,
        refresh,
        submit: () => {
          // ``submit`` is only called by the stream consumer's
          // internal plumbing; the realtime path routes through
          // ``sendMessage`` -> ``startTurn`` instead. Providing a no-op
          // keeps the BaseStream contract satisfied for the few places
          // that might introspect the shape.
        },
        threadId: activeThreadId || null,
        compact,
        vitals,
      }) as ExposedRealtimeThread & {
        compact: typeof compact;
        vitals: typeof vitals;
      },
    [
      mapped,
      visibleMessages,
      streamingMessage,
      isUiLoading,
      isThreadLoading,
      error,
      stop,
      refresh,
      activeThreadId,
      vitals,
      compact,
    ],
  );

  const deliverOutbound = useCallback(
    (outbound: PendingOutboundMessage) => {
      const rawText = outbound.message.text.trim();
      const parsedComposerMode = parseCodexComposerModeMarker(rawText);
      const text = parsedComposerMode.text.trim();
      const files = outbound.message.files;
      const hasFileUploads = files.length > 0;
      updateOptimisticMessages({
        type: "set-delivery",
        clientMessageId: outbound.clientMessageId,
        deliveryState: transportReady ? "sending" : "queued",
      });
      void (async () => {
        setSendError(null);
        if (outbound.intent === "steer") {
          if (!runningTurnId || runningTurnId !== outbound.targetTurnId) {
            throw new Error(t.conversation.steeringTurnUnavailable);
          }
          if (files.length > 0) {
            throw new Error(
              "Files cannot be added while the current task is running; send a text correction or stop first.",
            );
          }
          await steer({ input: text, itemId: outbound.clientMessageId });
          return;
        }
        if (isLoading) {
          throw new Error(t.conversation.previousMessagePending);
        }
        setIsUploading(hasFileUploads);
        try {
          const attachments =
            outbound.threadId && outbound.threadId !== "new"
              ? await uploadPromptInputFiles(outbound.threadId, files)
              : await fallbackFileAttachmentsAsync(files);
          // No upload toast here: the composer chip owns that signal now. A
          // floating "uploaded 1 file" popup fired at send time told the user
          // about work they had already watched finish, and it appeared
          // detached from the attachment it described.
          const rawContext =
            context && typeof context === "object"
              ? (context as Record<string, unknown>)
              : {};
          const explicitMode = stringValue(rawContext.mode);
          const selectedMode = explicitMode ?? "code";
          const explicitCapabilityMode = stringValue(
            rawContext.capability_mode,
          );
          const explicitCodeMode = stringValue(rawContext.code_mode);
          const shouldDefaultCodeCapability =
            !explicitMode || selectedMode === "code";
          const runtimeContext = applyCodexComposerModeContext(
            stripUndefinedValues({
              ...rawContext,
              mode: selectedMode,
              capability_mode:
                explicitCapabilityMode ??
                (shouldDefaultCodeCapability ? "code" : undefined),
              code_mode:
                explicitCodeMode ??
                (shouldDefaultCodeCapability ? "solo" : undefined),
              permission_mode: permissionRuntime.mode,
              sandbox_mode: permissionRuntime.sandbox_mode,
              execution_environment: permissionRuntime.execution_environment,
            }),
            parsedComposerMode.mode,
          );
          const reasoningEffort = reasoningEffortValue(
            rawContext["reasoning_effort"],
          );
          const topologyId = topologyIdValue(rawContext);
          const metadataContext = reasoningEffort
            ? { ...runtimeContext, reasoning_effort: reasoningEffort }
            : runtimeContext;
          const projectCwd = stringValue(metadataContext.workspace_path);
          setIsUploading(false);
          await startTurn({
            input: text,
            ...(projectCwd ? { cwd: projectCwd } : {}),
            clientItemId: outbound.clientMessageId,
            attachments,
            approvalPolicy: effectiveApprovalPolicy,
            sandboxPolicy: effectiveSandboxPolicy,
            ...(permissionRuntime.planningMode ? { planningMode: true } : {}),
            ...(parsedComposerMode.mode === "plan" ||
            parsedComposerMode.mode === "spec"
              ? { planningMode: true }
              : {}),
            ...(model ? { model } : {}),
            ...(reasoningEffort ? { effort: reasoningEffort } : {}),
            ...(topologyId ? { topologyId } : {}),
            metadata: {
              context: metadataContext,
            } as Record<string, unknown>,
          });
        } finally {
          setIsUploading(false);
        }
      })().catch((err) => {
        // Reaching here means the turn was never delivered: startTurn
        // resolves (not rejects) when a socket drop happens after the
        // turn/started notification was observed, so mid-turn
        // disconnects of an already-persisted message don't land in
        // this catch.
        // JSON-RPC failures can cross the WebSocket boundary as plain objects,
        // so `instanceof Error` loses their actionable `message` field.
        const errorMessage = getStreamErrorMessage(
          err,
          t.streaming?.networkLost ?? "Connection unavailable.",
        );
        setSendError(errorMessage);
        updateOptimisticMessages({
          type: "set-delivery",
          clientMessageId: outbound.clientMessageId,
          deliveryState: "failed",
          error: errorMessage,
        });
        if (hasFileUploads) {
          toast.error(t.chatInputBox.uploadFailed);
        }
        // Keep the failed bubble in the timeline and also hand the original
        // draft back to the composer so the user can edit before retrying.
        if (typeof window !== "undefined") {
          void extractImageFiles(files)
            .catch(() => [])
            .then((images) => {
              window.dispatchEvent(
                new CustomEvent("echo:send-failed", {
                  detail: {
                    threadId: outbound.threadId,
                    clientMessageId: outbound.clientMessageId,
                    text: rawText,
                    images,
                  },
                }),
              );
            });
        }
      });
    },
    [
      startTurn,
      steer,
      isLoading,
      runningTurnId,
      effectiveApprovalPolicy,
      effectiveSandboxPolicy,
      model,
      context,
      permissionRuntime.mode,
      permissionRuntime.planningMode,
      permissionRuntime.sandbox_mode,
      permissionRuntime.execution_environment,
      transportReady,
      t.chatInputBox.uploadFailed,
      t.streaming?.networkLost,
      t.conversation.previousMessagePending,
      t.conversation.steeringTurnUnavailable,
      updateOptimisticMessages,
    ],
  );

  useEffect(() => {
    if (!transportReady) return;
    const queued = pendingOutboundRef.current.filter(
      (message) => message.deliveryState === "queued",
    );
    // Normal UI flow permits only one unacknowledged start. Keep this loop so
    // restored steering rows still preserve FIFO if a reconnect races them.
    for (const outbound of queued) {
      deliverOutbound(outbound);
    }
  }, [deliverOutbound, transportReady]);

  useEffect(() => {
    const handleRetry = (
      event: CustomEvent<{
        threadId?: string | null;
        clientMessageId?: string | null;
      }>,
    ) => {
      const detail = event.detail;
      if (detail?.threadId && detail.threadId !== threadId) return;
      const clientMessageId = detail?.clientMessageId;
      if (!clientMessageId) return;
      const pending = pendingOutboundRef.current.find(
        (message) =>
          message.clientMessageId === clientMessageId &&
          message.deliveryState === "failed",
      );
      if (!pending) return;
      const waitingForFirstTurnReceipt = pendingOutboundRef.current.some(
        (message) =>
          message.clientMessageId !== clientMessageId &&
          message.deliveryState !== "failed",
      );
      if (waitingForFirstTurnReceipt) {
        setSendError(t.conversation.previousMessagePending);
        return;
      }
      if (pending.intent === "start" && isLoading) {
        setSendError(t.conversation.previousMessagePending);
        return;
      }
      if (
        pending.intent === "steer" &&
        (!runningTurnId || runningTurnId !== pending.targetTurnId)
      ) {
        setSendError(t.conversation.steeringTurnUnavailable);
        return;
      }
      deliverOutbound(pending);
    };
    window.addEventListener(
      RETRY_PENDING_MESSAGE_EVENT,
      handleRetry as EventListener,
    );
    return () => {
      window.removeEventListener(
        RETRY_PENDING_MESSAGE_EVENT,
        handleRetry as EventListener,
      );
    };
  }, [
    deliverOutbound,
    isLoading,
    runningTurnId,
    t.conversation.previousMessagePending,
    t.conversation.steeringTurnUnavailable,
    threadId,
  ]);

  const sendMessage = useCallback<SendMessageFn>(
    (_threadId, message) => {
      const rawText = (message?.text ?? "").trim();
      const parsedComposerMode = parseCodexComposerModeMarker(rawText);
      const displayText = parsedComposerMode.text.trim();
      const files = message.files ?? [];
      if (!displayText && files.length === 0) return;
      const effectiveThreadId =
        _threadId && _threadId !== "new" ? _threadId : threadId;
      const outbound: PendingOutboundMessage = {
        clientMessageId: newClientMessageId(),
        threadId: effectiveThreadId,
        intent: runningTurnId ? "steer" : "start",
        ...(runningTurnId ? { targetTurnId: runningTurnId } : {}),
        message: { text: rawText, files },
        displayText,
        createdAt: new Date().toISOString(),
        deliveryState: transportReady ? "sending" : "queued",
      };
      const waitingForFirstTurnReceipt =
        !isLoading &&
        pendingOutboundRef.current.some(
          (pending) => pending.deliveryState !== "failed",
        );
      if (waitingForFirstTurnReceipt) {
        const errorMessage = t.conversation.previousMessagePending;
        const failed = {
          ...outbound,
          deliveryState: "failed" as const,
          error: errorMessage,
        };
        updateOptimisticMessages({ type: "enqueue", message: failed });
        setSendError(errorMessage);
        if (typeof window !== "undefined") {
          window.dispatchEvent(
            new CustomEvent("echo:send-failed", {
              detail: {
                threadId: effectiveThreadId,
                clientMessageId: outbound.clientMessageId,
                text: rawText,
                images: [],
              },
            }),
          );
        }
        return;
      }
      updateOptimisticMessages({ type: "enqueue", message: outbound });
      if (transportReady) {
        deliverOutbound(outbound);
      }
    },
    [
      deliverOutbound,
      isLoading,
      runningTurnId,
      t.conversation.previousMessagePending,
      threadId,
      transportReady,
      updateOptimisticMessages,
    ],
  );

  return [
    exposedThread,
    sendMessage,
    isUploading,
    liveToolEvents,
    lastTurnToolEvents,
    approvalControls,
  ] as const;
}

/** Exported for tests: the attach-time-upload reuse path is worth pinning. */
export async function uploadPromptInputFiles(
  threadId: string,
  fileParts: PromptInputMessage["files"],
): Promise<Record<string, unknown>[]> {
  if (fileParts.length === 0) return [];
  const files = (
    await Promise.all(fileParts.map((part) => promptInputFilePartToFile(part)))
  ).filter((file): file is File => file instanceof File);
  if (files.length === 0) return fallbackFileAttachments(fileParts);
  // The composer now uploads on attach, so by send time the bytes are usually
  // already on the server. Re-posting them would double the wait and mint a
  // second artifact for the same picture.
  const preUploaded = fileParts
    .map((part) => part.uploaded)
    .filter((info): info is UploadedFileInfo => !!info);
  const result =
    preUploaded.length === files.length
      ? { files: preUploaded }
      : await uploadFiles(threadId, files);

  // Hosted upload gives us a server-side path/URL. For image-typed
  // attachments we ALSO embed a base64 data URL so the backend can
  // build OpenAI image_url content blocks for vision models without
  // having to re-fetch the artifact.
  const fileByName = new Map<string, File>();
  for (const file of files) fileByName.set(file.name, file);
  const enriched = await Promise.all(
    result.files.map(async (uploaded) => {
      const base = uploadedFileToAttachment(uploaded);
      const file = fileByName.get(uploaded.filename);
      if (!file || !isImageMime(file.type)) return base;
      const dataUrl = await readFileAsDataUrl(file).catch(() => null);
      return dataUrl
        ? { ...base, mediaType: file.type, data_url: dataUrl }
        : base;
    }),
  );
  return enriched;
}

function uploadedFileToAttachment(
  file: UploadedFileInfo,
): Record<string, unknown> {
  return {
    filename: file.filename,
    size: file.size,
    path: file.path,
    virtual_path: file.virtual_path,
    artifact_url: file.artifact_url,
    extension: file.extension,
    modified: file.modified,
    extracted_text: file.extracted_text ?? null,
  };
}

function fallbackFileAttachments(
  fileParts: PromptInputMessage["files"],
): Record<string, unknown>[] {
  return fileParts.map((part) => ({
    filename: part.filename,
    mediaType: part.mediaType,
    url: part.url,
  }));
}

/**
 * Variant of fallbackFileAttachments used when the runtime hasn't
 * created the thread yet. We can't upload to the artifact store, but
 * we CAN base64-encode any image attachments inline so the model sees
 * them on the very first turn.
 */
async function fallbackFileAttachmentsAsync(
  fileParts: PromptInputMessage["files"],
): Promise<Record<string, unknown>[]> {
  return Promise.all(
    fileParts.map(async (part) => {
      const base: Record<string, unknown> = {
        filename: part.filename,
        mediaType: part.mediaType,
        url: part.url,
      };
      if (!isImageMime(part.mediaType)) return base;
      const file = await promptInputFilePartToFile(part);
      if (!(file instanceof File)) return base;
      const dataUrl = await readFileAsDataUrl(file).catch(() => null);
      return dataUrl ? { ...base, data_url: dataUrl } : base;
    }),
  );
}

function isImageMime(mediaType: string | undefined | null): boolean {
  return (
    typeof mediaType === "string" &&
    mediaType.toLowerCase().startsWith("image/")
  );
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result === "string") resolve(result);
      else reject(new Error("FileReader returned non-string"));
    };
    reader.onerror = () =>
      reject(reader.error ?? new Error("FileReader failed"));
    reader.readAsDataURL(file);
  });
}

async function extractImageFiles(
  fileParts: PromptInputMessage["files"],
): Promise<File[]> {
  const recovered = await Promise.all(
    fileParts.map((part) => promptInputFilePartToFile(part)),
  );
  return recovered.filter(
    (file): file is File =>
      file instanceof File && isImageMime(file.type || "image/*"),
  );
}
