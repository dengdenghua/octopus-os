import {
  stripInternalToolProtocol,
  stripLeakedRendererMarkup,
} from "@/core/messages/utils";

export const AGENT_WORKBENCH_FOCUS_EVENT = "echo:agent-workbench-focus";
export const AGENT_WORKBENCH_LOCATE_EVENT = "echo:agent-workbench-locate";
export const AGENT_WORKBENCH_OPEN_EVENT = "echo:agent-workbench-open";

export type AgentWorkbenchTab =
  | "agent"
  | "subagents"
  | "artifacts"
  | "plan"
  | "diff"
  | "terminal"
  | "browser";

/** Sub-view of the per-agent workbench page: "summary" is the overview,
 * "screen" is the agent's independent process, "role" is the role card
 * (工牌). Omitted = panel default. */
export type AgentWorkbenchFocusView = "summary" | "screen" | "role";
export type AgentWorkbenchEventView = "summary" | "trace" | "screen";
export type AgentWorkbenchProcessEventKind = "thinking" | "execution";

/** Public identity carried with a focus intent so a historical conversation
 * card remains inspectable after the workbench has advanced to a newer turn. */
export type AgentWorkbenchFocusAgentSnapshot = {
  id: string;
  name: string;
  role?: string;
  avatar?: string;
  status: "running" | "done" | "error" | "waiting";
  task: string;
  summary?: string;
  iterationCount?: number;
  filesTouchedCount?: number;
  error?: string;
  index?: number;
};

export type AgentWorkbenchProcessEventSnapshot = {
  /** Only explicitly public text belongs here; never raw provider reasoning. */
  summary: string;
  detail?: string;
  kind: AgentWorkbenchProcessEventKind;
  status?: "running" | "waiting" | "error" | "pending" | "done";
  count?: number;
  phaseId?: string;
  parentItemId?: string;
  timelineSequence?: number;
};

export type AgentWorkbenchFocusDetail = {
  agentId: string;
  agent?: AgentWorkbenchFocusAgentSnapshot;
  /** Owning conversation turn. Lets the workbench replay a historical run
   * instead of silently falling back to the latest turn. */
  turnIndex?: number;
  tab?: AgentWorkbenchTab;
  view?: AgentWorkbenchFocusView;
};

export type AgentWorkbenchOpenDetail = {
  tab?: AgentWorkbenchTab;
  /** Stable id shared by the transcript event and its workbench block. */
  eventId?: string;
  /** Durable external-effect receipt selected from the transcript. */
  effectKey?: string;
  eventKind?: AgentWorkbenchProcessEventKind;
  /** Public snapshot used when the selected transcript row has no tool block. */
  processEvent?: AgentWorkbenchProcessEventSnapshot;
  /** The workbench surface that best explains the selected event. */
  view?: AgentWorkbenchEventView;
};

export type AgentWorkbenchLocateDetail = {
  /** Stable transcript row id from data-process-event-id. */
  eventId: string;
};

const INTERNAL_PROCESS_EVENT_BLOCK_RE =
  /`?<(?:(?:Reasoning|ToolCall|ToolResult|Thinking|Execution)Block)\b[^<>`]*>[\s\S]*?<\/(?:(?:Reasoning|ToolCall|ToolResult|Thinking|Execution)Block)>`?/g;
const PROCESS_EVENT_SECRET_RE =
  /\b(?:sk|pk|rk|ghp|gho|ghs|ghu|xox[baprs])[-_][A-Za-z0-9]{8,}\b|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}|\b(?:Bearer|Authorization:?)\s+[A-Za-z0-9._-]{10,}|(["']?(?:api[_-]?key|secret|password|passwd|token)["']?\s*[:=]\s*)["']?[^\s"',}]{4,}/gi;
const PROCESS_EVENT_RAW_TOOL_RE =
  /\b(?:read_file|exec_shell|shell_command|run_command|todo_write|apply_patch|write_file|edit_file|str_replace)\b/gi;
const PROCESS_EVENT_PROTOCOL_PREFIX_RE =
  /^\s*(?:Thought|Action|Observation|Final Answer|Tool|Tool Result)\s*:\s*/gim;

function sanitizeProcessEventText(value: string | undefined): string {
  if (!value) return "";
  return stripLeakedRendererMarkup(
    stripInternalToolProtocol(
      value.replace(INTERNAL_PROCESS_EVENT_BLOCK_RE, ""),
    ),
  )
    .replace(PROCESS_EVENT_SECRET_RE, (_match, prefix: string | undefined) =>
      prefix ? `${prefix}«redacted»` : "«redacted»",
    )
    .replace(PROCESS_EVENT_RAW_TOOL_RE, "operation")
    .replace(PROCESS_EVENT_PROTOCOL_PREFIX_RE, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function firstPublicLine(value: string): string {
  return (
    value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find(Boolean) ?? ""
  );
}

function fallbackProcessSummary(
  processEvent: AgentWorkbenchProcessEventSnapshot,
): string {
  const fromDetail = firstPublicLine(
    sanitizeProcessEventText(processEvent.detail),
  );
  if (fromDetail) return fromDetail;
  return "…";
}

export function sanitizeWorkbenchOpenDetail(
  detail: AgentWorkbenchOpenDetail,
): AgentWorkbenchOpenDetail {
  if (!detail.processEvent) return detail;
  const summary = sanitizeProcessEventText(detail.processEvent.summary);
  const detailText = sanitizeProcessEventText(detail.processEvent.detail);
  const safeSummary = summary || fallbackProcessSummary(detail.processEvent);
  return {
    ...detail,
    processEvent: {
      ...detail.processEvent,
      summary: safeSummary,
      detail: detailText || safeSummary,
    },
  };
}

export function emitAgentWorkbenchFocus(detail: AgentWorkbenchFocusDetail) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<AgentWorkbenchFocusDetail>(AGENT_WORKBENCH_FOCUS_EVENT, {
      detail,
    }),
  );
}

export function emitOpenAgentWorkbench(detail?: AgentWorkbenchOpenDetail) {
  if (typeof window === "undefined") return;
  const safeDetail = sanitizeWorkbenchOpenDetail(detail ?? {});
  window.dispatchEvent(
    new CustomEvent<AgentWorkbenchOpenDetail>(AGENT_WORKBENCH_OPEN_EVENT, {
      detail: safeDetail,
    }),
  );
}

export function emitLocateAgentWorkbenchEvent(
  detail: AgentWorkbenchLocateDetail,
) {
  if (typeof window === "undefined") return;
  const eventId = detail.eventId.trim();
  if (!eventId) return;
  window.dispatchEvent(
    new CustomEvent<AgentWorkbenchLocateDetail>(AGENT_WORKBENCH_LOCATE_EVENT, {
      detail: { eventId },
    }),
  );
}
