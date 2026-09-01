/**
 * Normalizer for the typed sub-agent event bus.
 *
 * The backend exposes an SSE endpoint (`/api/subagents/stream/{root}`) that
 * replays + live-streams typed lifecycle events for a coordination root
 * (`sub_started` / `sub_tool_start` / `sub_tool_end` / `sub_concluded` /
 * `sub_incomplete` / `sub_failed`). This module turns those wire events into
 * the `LiveToolEvent` shape the AgentWorkbench panel already renders, so a
 * sub-agent's full run can be shown in an independent workbench window
 * without relying on the parent conversation stream.
 */
import type { LiveToolEvent } from "@/components/workspace/live-tool-timeline";

export interface SubAgentBusEvent {
  type: string;
  thread_id?: string;
  root_thread_id?: string;
  parent_thread_id?: string;
  seq?: number;
  ts?: number;
  payload: Record<string, unknown>;
}

const SUB_STARTED = "sub_started";
const SUB_TOOL_START = "sub_tool_start";
const SUB_TOOL_END = "sub_tool_end";
const SUB_CONCLUDED = "sub_concluded";
const SUB_INCOMPLETE = "sub_incomplete";
const SUB_FAILED = "sub_failed";

export const SUBAGENT_BUS_TYPES = new Set([
  SUB_STARTED,
  SUB_TOOL_START,
  SUB_TOOL_END,
  SUB_CONCLUDED,
  SUB_INCOMPLETE,
  SUB_FAILED,
]);

function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function toMillis(tsSeconds: unknown): number {
  const s = num(tsSeconds);
  if (s === undefined) return Date.now();
  return Math.round(s * 1000);
}

/** Map one typed bus event to a `LiveToolEvent` for the workbench. */
export function busEventToLiveEvent(
  event: SubAgentBusEvent,
  index: number,
): LiveToolEvent | null {
  const { type, payload } = event;
  const role = str(payload.role) ?? "";
  const startedAt = toMillis(event.ts);
  const baseId = `${type}:${event.thread_id ?? event.root_thread_id ?? "root"}:${event.seq ?? index}`;
  // Group key: the child's own codename (unique per sub-agent) so the
  // grouped timeline shows each sub-agent as its own lane. Falling back to
  // role keeps same-role children visually distinct only when no codename is
  // present (a coordination root usually has one codename per child).
  const agentId =
    (str(payload.requested_agent_id) ??
      str(payload.agent_id) ??
      str(payload.codename) ??
      role) ||
    undefined;
  const common = {
    agentId,
    subAgentRole: role || undefined,
    subagentCodename: str(payload.codename),
    iteration: num(payload.iteration) ?? 0,
    parentToolUseId:
      str(payload.parent_tool_use_id) ?? str(payload.parentToolUseId),
  };

  switch (type) {
    case SUB_STARTED: {
      const avatar = str(payload.avatar);
      return {
        id: baseId,
        name: str(payload.codename) ?? role,
        status: "running",
        startedAt,
        lifecycle: "spawned",
        subagentAvatar: avatar,
        input: {
          prompt_preview: str(payload.prompt_preview),
        },
        ...common,
      };
    }
    case SUB_TOOL_START: {
      const toolCallId = str(payload.tool_call_id);
      return {
        id: toolCallId
          ? `subagent-tool:${agentId ?? "agent"}:${toolCallId}`
          : baseId,
        name: str(payload.tool) ?? "tool",
        status: "running",
        startedAt,
        input:
          payload.input && typeof payload.input === "object"
            ? (payload.input as Record<string, unknown>)
            : undefined,
        ...common,
      };
    }
    case SUB_TOOL_END: {
      const toolCallId = str(payload.tool_call_id);
      const isError =
        str(payload.status) === "error" || payload.status === "failed";
      return {
        id: toolCallId
          ? `subagent-tool:${agentId ?? "agent"}:${toolCallId}`
          : baseId,
        name: str(payload.tool) ?? "tool",
        status: isError ? "error" : "done",
        startedAt,
        durationMs: num(payload.duration_ms),
        error: isError ? str(payload.error) : undefined,
        output: payload.output_preview,
        ...common,
      };
    }
    case SUB_CONCLUDED: {
      return {
        id: baseId,
        name: role || "subagent",
        status: payload.ok === false ? "error" : "done",
        startedAt,
        lifecycle: "finished",
        iterationCount: num(payload.iteration_count),
        filesTouched: Array.isArray(payload.files_touched)
          ? payload.files_touched.filter(
              (path): path is string => typeof path === "string",
            )
          : undefined,
        observation: str(payload.output),
        ...common,
      };
    }
    case SUB_INCOMPLETE: {
      return {
        id: baseId,
        name: role || "subagent",
        status: "error",
        error: `未完成 · ${str(payload.reason) ?? ""}（${num(payload.rounds) ?? 0} 轮）`,
        startedAt,
        lifecycle: "finished",
        ...common,
      };
    }
    case SUB_FAILED: {
      return {
        id: baseId,
        name: role || "subagent",
        status: "error",
        error: str(payload.error) ?? "失败",
        startedAt,
        lifecycle: "finished",
        ...common,
      };
    }
    default:
      return null;
  }
}
