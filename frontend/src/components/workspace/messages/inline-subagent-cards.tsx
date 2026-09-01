import {
  BotIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  MonitorIcon,
  UsersIcon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";
import { useId, useMemo, useState } from "react";
import type { LiveToolEvent } from "@/components/workspace/live-tool-timeline";
import type { AIMessage, Message, ToolMessage } from "@/core/api/types";
import { isTeammateToolName } from "@/components/workspace/messages/action-display";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import { emitAgentWorkbenchFocus } from "@/components/workspace/agent-workbench-events";
import { isInternalAutoParallelFailure } from "@/components/workspace/agent-workbench-utils";

type InlineSubagentStatus = "running" | "done" | "error" | "waiting";

const MAX_COLLAPSED_AGENTS = 6;

export interface InlineSubagentInfo {
  id: string;
  name: string;
  role?: string;
  avatar?: string;
  status: InlineSubagentStatus;
  task: string;
  summary?: string;
  filesTouchedCount: number;
  iterationCount?: number;
  error?: string;
  index?: number;
  /** Progress 0-1. Undefined means "just spawned, no activity yet".
   *  Done agents always have progress=1 and are rendered as ✓ instead. */
  progress?: number;
}

function firstString(
  input: Record<string, unknown> | undefined,
  keys: string[],
): string {
  if (!input) return "";
  for (const key of keys) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

/** Turn the model-facing custom-role wrapper into the one-line task brief a
 * human expects on the Kimi-style cluster card. The full prompt remains in
 * the detail/workbench view. */
export function compactSubagentTask(value: string): string {
  let task = value.trim();
  if (/^#\s*Role:/i.test(task)) {
    const separator = task.match(/\n\s*---\s*\n/);
    if (separator?.index !== undefined) {
      task = task.slice(separator.index + separator[0].length).trim();
    }
  }
  task = task.replace(/\s+/g, " ").trim();
  return task.length > 220 ? `${task.slice(0, 219).trimEnd()}…` : task;
}

/** Extract progress (0-1) from a LiveToolEvent's input.progress or iteration info.
 *  Priority: input.progress.percent > iteration/iterationCount > null (no info). */
function extractProgressFromEvent(
  event: LiveToolEvent,
  totalIterations?: number,
): number | null {
  // 1. Direct percent from input.progress
  const progressObj = event.input?.progress;
  if (
    progressObj &&
    typeof progressObj === "object" &&
    !Array.isArray(progressObj)
  ) {
    const p = (progressObj as Record<string, unknown>).percent;
    if (typeof p === "number" && Number.isFinite(p)) {
      // Normalize: 0-1 → as-is, 0-100 → divide by 100
      const normalized = p > 1 ? p / 100 : p;
      return Math.max(0, Math.min(1, normalized));
    }
    const current = (progressObj as Record<string, unknown>).current;
    const total = (progressObj as Record<string, unknown>).total;
    if (typeof current === "number" && typeof total === "number" && total > 0) {
      return Math.max(0, Math.min(1, current / total));
    }
  }
  // 2. Iteration-based: event.iteration / totalIterations
  if (
    typeof event.iteration === "number" &&
    typeof totalIterations === "number" &&
    totalIterations > 0
  ) {
    return Math.max(0, Math.min(1, event.iteration / totalIterations));
  }
  return null;
}

/** Fallback: estimate progress from activity event count when no real progress data exists.
 *  Uses a slow saturating curve so it doesn't "fill up immediately".
 *  count=0 → 0.1 (just spawned, 1 col dim), 1→0.2, 3→0.4, 7→0.8, 9+→0.9 */
function estimateProgressFromEventCount(count: number): number {
  if (count <= 0) return 0.1;
  return Math.min(0.9, 0.1 + count * 0.1);
}

/** Compute a stable lane ID that matches message-derived specs.
 *  A role is not an identity: parallel custom agents can both resolve to
 *  ``explorer``. The realtime adapter normalizes ``agentId`` to the requested
 *  spec id (or a unique codename for legacy history), so role is last-resort. */
function stableAgentKey(event: LiveToolEvent): string | null {
  const agentId =
    event.agentId &&
    event.agentId !== "__main__" &&
    event.agentId !== event.subAgentRole
      ? event.agentId
      : null;
  return (
    agentId ??
    event.subagentCodename ??
    (event.agentId && event.agentId !== "__main__" ? event.agentId : null) ??
    event.agentName ??
    event.subAgentRole ??
    null
  );
}

export function deriveInlineSubagents(
  events: LiveToolEvent[],
): InlineSubagentInfo[] {
  // Phase 1: Build agentId → stableKey mapping from lifecycle/subagent events.
  // When a subagent is spawned, we get both its runtime agentId (UUID) and its role/codename.
  // We map the UUID to the human-readable role so later tool events (which only carry agentId)
  // get attributed to the same key as the spawn event (and match message-derived specs).
  const idMap = new Map<string, string>(); // runtime/codename alias → requested lane id
  for (const event of events) {
    const key = stableAgentKey(event);
    if (!key) continue;
    for (const alias of [event.agentId, event.subagentCodename]) {
      if (alias && alias !== "__main__" && alias !== key) {
        idMap.set(alias, key);
      }
    }
  }

  /** Resolve any event to its canonical stable key, using the id mapping. */
  const resolveKey = (e: LiveToolEvent): string | null => {
    if (e.agentId && e.agentId !== "__main__") {
      const mapped = idMap.get(e.agentId);
      if (mapped) return mapped;
    }
    if (e.subagentCodename) {
      const mapped = idMap.get(e.subagentCodename);
      if (mapped) return mapped;
    }
    return stableAgentKey(e);
  };

  const byId = new Map<string, InlineSubagentInfo>();
  const activityCount = new Map<string, number>();
  const totalIterations = new Map<string, number>();
  const realProgress = new Map<string, number>();

  // Phase 2: Single pass to accumulate counts and build agent records using resolved keys.
  for (const event of events) {
    if (isInternalAutoParallelFailure(event)) continue;
    const key = resolveKey(event);
    const isLifecycle =
      event.lifecycle === "spawned" || event.lifecycle === "finished";
    const isSubagentMarker = event.name === "subagent";
    const isSubagentEvent =
      isLifecycle ||
      Boolean(event.subagentCodename) ||
      (Boolean(event.parentToolUseId) && Boolean(event.subAgentRole)) ||
      (Boolean(event.agentId) &&
        event.agentId !== "__main__" &&
        Boolean(event.agentId && idMap.has(event.agentId)));

    if (key) {
      if (
        event.lifecycle === "finished" &&
        typeof event.iterationCount === "number"
      ) {
        totalIterations.set(key, event.iterationCount);
      }
      const prog = extractProgressFromEvent(event, totalIterations.get(key));
      if (prog !== null) {
        const existing = realProgress.get(key);
        if (existing === undefined || prog > existing)
          realProgress.set(key, prog);
      }
      if (!isLifecycle && !isSubagentMarker && isSubagentEvent) {
        activityCount.set(key, (activityCount.get(key) ?? 0) + 1);
      }
    }

    if (!isSubagentEvent || !key || key === "__main__") continue;

    const existing = byId.get(key);
    const outputObj = event.output as Record<string, unknown> | undefined;
    const outputIsString = typeof event.output === "string";
    const outputError = firstString(outputObj, ["error", "error_type"]);
    const outputIndicatesError =
      outputObj?.ok === false || Boolean(outputError);
    const isTerminalAgentEvent =
      event.lifecycle === "finished" ||
      (isSubagentMarker &&
        event.lifecycle === undefined &&
        (event.status === "done" || event.status === "error"));
    const status: InlineSubagentStatus =
      isTerminalAgentEvent
        ? event.status === "error" || outputIndicatesError
          ? "error"
          : "done"
        : event.status === "waiting_approval"
          ? "waiting"
          : "running";

    const task = compactSubagentTask(
      firstString(event.input as Record<string, unknown> | undefined, [
        "prompt_preview",
        "prompt",
        "task",
        "description",
        "query",
        "message",
      ]) ||
        event.thought ||
        existing?.task ||
        "",
    );
    const summary =
      isTerminalAgentEvent
        ? (outputIsString
            ? (event.output as string)
            : firstString(outputObj, [
                "summary",
                "result",
                "output",
                "thought",
                "observation",
                "answer",
                "content",
              ])) ||
          event.thought ||
          event.observation ||
          existing?.summary
        : existing?.summary;
    const filesTouched = Array.isArray(outputObj?.files_touched)
      ? (outputObj!.files_touched as unknown[]).filter(
          (p): p is string => typeof p === "string",
        )
      : (event.filesTouched ?? existing?.filesTouchedCount ?? 0);
    const inputName = firstString(
      event.input as Record<string, unknown> | undefined,
      ["name", "display_name"],
    );
    const errorMsg =
      isTerminalAgentEvent && status === "error"
        ? (outputIsString
            ? (event.output as string)
            : outputError || firstString(outputObj, ["message"])) ||
          event.thought ||
          existing?.error
        : existing?.error;

    let progress: number;
    if (status === "done") {
      progress = 1.0;
    } else if (status === "error") {
      progress =
        realProgress.get(key) ??
        (totalIterations.get(key) && typeof event.iteration === "number"
          ? Math.min(0.9, event.iteration / totalIterations.get(key)!)
          : null) ??
        ((activityCount.get(key) ?? 0) > 0
          ? estimateProgressFromEventCount(activityCount.get(key)!)
          : 0.3);
    } else {
      progress =
        realProgress.get(key) ??
        (totalIterations.get(key) && typeof event.iteration === "number"
          ? Math.min(0.9, event.iteration / totalIterations.get(key)!)
          : null) ??
        estimateProgressFromEventCount(activityCount.get(key) ?? 0);
    }

    byId.set(key, {
      id: key,
      name:
        inputName ||
        event.subagentCodename ||
        existing?.name ||
        event.subAgentRole ||
        event.agentName ||
        key,
      role: event.subAgentRole ?? existing?.role,
      avatar: event.subagentAvatar ?? existing?.avatar,
      status:
        existing?.status === "done" || existing?.status === "error"
          ? existing.status
          : status,
      task: task || existing?.task || "",
      summary:
        summary ||
        event.observation?.slice(0, 200) ||
        (event.thought ? event.thought.slice(0, 200) : existing?.summary),
      filesTouchedCount: Array.isArray(filesTouched)
        ? Math.max(filesTouched.length, existing?.filesTouchedCount ?? 0)
        : typeof filesTouched === "number"
          ? Math.max(filesTouched, existing?.filesTouchedCount ?? 0)
          : (existing?.filesTouchedCount ?? 0),
      iterationCount:
        event.iterationCount ??
        (typeof outputObj?.iteration_count === "number"
          ? (outputObj.iteration_count as number)
          : existing?.iterationCount),
      error:
        isTerminalAgentEvent && status === "done"
          ? undefined
          : errorMsg,
      progress,
    });
  }
  return Array.from(byId.values());
}

function parseToolContent(msg: ToolMessage): unknown {
  const content = msg.content;
  if (typeof content !== "string") return null;
  try {
    return JSON.parse(content);
  } catch {
    return content;
  }
}

function roleEmoji(role?: string): string | undefined {
  if (!role) return undefined;
  const r = role.toLowerCase();
  if (r.includes("research") || r.includes("搜索") || r.includes("调研"))
    return "🔍";
  if (
    r.includes("code") ||
    r.includes("coder") ||
    r.includes("开发") ||
    r.includes("编程")
  )
    return "💻";
  if (r.includes("write") || r.includes("writer") || r.includes("写作"))
    return "✍️";
  if (r.includes("review") || r.includes("审查") || r.includes("评审"))
    return "👀";
  if (r.includes("design") || r.includes("设计")) return "🎨";
  if (r.includes("test") || r.includes("测试")) return "🧪";
  if (r.includes("plan") || r.includes("规划")) return "📋";
  if (r.includes("analysis") || r.includes("分析")) return "📊";
  return undefined;
}

export function deriveSubagentsFromMessages(
  messages: Message[],
): InlineSubagentInfo[] {
  const results: InlineSubagentInfo[] = [];
  const toolResults = new Map<string, { data: unknown; error: boolean }>();

  for (const msg of messages) {
    if (msg.type === "tool" && msg.tool_call_id) {
      const parsed = parseToolContent(msg as ToolMessage);
      toolResults.set(msg.tool_call_id, {
        data: parsed,
        error: (msg as ToolMessage).status === "error",
      });
    }
  }

  let agentIndex = 0;
  for (const msg of messages) {
    if (msg.type !== "ai") continue;
    const aiMsg = msg as AIMessage;
    const toolCalls = aiMsg.tool_calls ?? [];

    for (const tc of toolCalls) {
      if (!isTeammateToolName(tc.name)) continue;
      // Legacy "task" tools are handled by SubtaskCard/ParallelSubtasksGrid, not here.
      if (tc.name.toLowerCase() === "task") continue;

      const args = (tc.args ?? {}) as Record<string, unknown>;
      if (
        tc.name.toLowerCase() === "subagent" &&
        firstString(args, ["error", "error_type"]).toLowerCase() ===
          "empty_result_contract_violation"
      ) {
        continue;
      }
      const toolResult = tc.id ? toolResults.get(tc.id) : undefined;
      const result = toolResult?.data;
      // Realtime history folds command execution back into
      // ``tool_call.args.output`` instead of always retaining a separate tool
      // message. Treat that envelope as a settled result too; otherwise an
      // invalid pre-spawn call remains "running" and is later promoted to a
      // fake completed Agent card when the turn settles.
      const embeddedOutput = firstString(args, ["output"]);
      const embeddedToolErrored =
        /^\s*\((?:工具失败|tool failed)\)/i.test(embeddedOutput) ||
        /\berror=structured_error\b/i.test(embeddedOutput);
      const hasToolResult = Boolean(toolResult) || Boolean(embeddedOutput);
      const toolErrored = Boolean(toolResult?.error) || embeddedToolErrored;

      const resultObj =
        result && typeof result === "object" && !Array.isArray(result)
          ? (result as Record<string, unknown>)
          : null;
      const successes = Array.isArray(resultObj?.successes)
        ? (resultObj!.successes as Array<Record<string, unknown>>)
        : [];
      const failures = Array.isArray(resultObj?.failures)
        ? (resultObj!.failures as Array<Record<string, unknown>>)
        : [];
      const resultIsString = typeof result === "string";

      const specs = Array.isArray(args.specs)
        ? (args.specs as Array<Record<string, unknown>>)
        : Array.isArray(args.agents)
          ? (args.agents as Array<Record<string, unknown>>)
          : Array.isArray(args.tasks)
            ? (args.tasks as Array<Record<string, unknown>>)
            : [];

      if (specs && specs.length > 0) {
        // The dispatcher rejected the batch before spawning any child. Keep
        // that failure in the execution trace, but do not manufacture one
        // Agent card per unexecuted spec.
        if (
          embeddedToolErrored &&
          successes.length === 0 &&
          failures.length === 0
        ) {
          continue;
        }
        for (let i = 0; i < specs.length; i++) {
          const spec = specs[i];
          if (!spec) continue;
          const agentId =
            typeof spec.agent_id === "string"
              ? spec.agent_id
              : typeof spec.name === "string"
                ? spec.name
                : typeof spec.role === "string"
                  ? spec.role
                : `spec-${i}`;
          const role = typeof spec.role === "string" ? spec.role : undefined;

          const success = successes.find(
            (s) =>
              (typeof s.agent_id === "string" && s.agent_id === agentId) ||
              (typeof s.spec_index === "number" && s.spec_index === i) ||
              (typeof s.role === "string" && role && s.role === role),
          );
          const failure = failures.find(
            (f) =>
              (typeof f.agent_id === "string" && f.agent_id === agentId) ||
              (typeof f.spec_index === "number" && f.spec_index === i) ||
              (typeof f.role === "string" && role && f.role === role),
          );

          const matched = success || failure;
          const displayName = matched
            ? firstString(matched, ["display_name", "name", "codename"])
            : undefined;
          const codename = matched
            ? typeof matched.codename === "string"
              ? matched.codename
              : undefined
            : undefined;
          const specName =
            typeof spec.name === "string"
              ? spec.name
              : typeof spec.codename === "string"
                ? spec.codename
                : undefined;
          const name = displayName || codename || specName || role || agentId;

          const task = compactSubagentTask(
            firstString(spec, [
              "prompt_preview",
              "prompt",
              "goal",
              "task",
              "description",
              "query",
              "message",
            ]) ||
              (matched
                ? firstString(matched, ["task_label", "task_preview"]) || ""
                : ""),
          );

          let status: InlineSubagentStatus = hasToolResult
            ? failure
              ? "error"
              : success
                ? "done"
                : toolErrored || resultObj?.ok === false
                  ? "error"
                  : hasToolResult
                    ? "done"
                    : "running"
            : "running";

          let summary = "";
          let error = "";
          let iterationCount: number | undefined;
          let filesTouched = 0;

          if (success) {
            summary =
              firstString(success, [
                "output",
                "result",
                "summary",
                "content",
              ]) || "";
            if (typeof success.iteration_count === "number")
              iterationCount = success.iteration_count;
            if (Array.isArray(success.files_touched))
              filesTouched = success.files_touched.length;
          } else if (failure) {
            error =
              firstString(failure, ["error", "message"]) ||
              (typeof failure.error_type === "string"
                ? failure.error_type
                : "");
            summary = firstString(failure, ["partial_output", "output"]) || "";
            if (typeof failure.iteration_count === "number")
              iterationCount = failure.iteration_count;
            if (Array.isArray(failure.files_touched))
              filesTouched = failure.files_touched.length;
          } else if (resultIsString && hasToolResult) {
            summary = result as string;
            if (toolErrored) error = result as string;
          } else if (resultObj && hasToolResult && !success && !failure) {
            summary =
              firstString(resultObj, [
                "output",
                "result",
                "summary",
                "content",
              ]) || "";
            error = firstString(resultObj, ["error", "message"]) || "";
            if (resultObj.ok === false || toolErrored) status = "error";
          }

          const resultAvatar =
            matched && typeof matched.avatar === "string"
              ? matched.avatar
              : undefined;
          const specAvatar =
            typeof spec.avatar === "string" ? spec.avatar : undefined;

          // Progress from message data: done/error → 1.0, running → no info (undefined → show starting state)
          const progress =
            status === "done" ? 1.0 : status === "error" ? 0.5 : undefined;

          results.push({
            id: agentId,
            name,
            role: role !== name ? role : undefined,
            avatar: resultAvatar ?? specAvatar ?? roleEmoji(role ?? name),
            status,
            task,
            summary: summary || undefined,
            filesTouchedCount: filesTouched,
            iterationCount,
            error: error || undefined,
            index: agentIndex++,
            progress,
          });
        }
      } else {
        // Single agent call (call_agent, spawn_agent, delegate_agent, or "subagent" record).
        // Also handles MCP server-prefixed names (e.g. "team.call_agent").
        const agentId =
          typeof args.requested_agent_id === "string"
            ? (args.requested_agent_id as string)
            : typeof args.requestedAgentId === "string"
              ? (args.requestedAgentId as string)
              : typeof args.agent_id === "string"
                ? (args.agent_id as string)
                : typeof args.subagent_id === "string"
                  ? (args.subagent_id as string)
                  : (tc.id ??
                    `call-${Math.random().toString(36).slice(2, 10)}`);
        const role =
          typeof args.role === "string" ? (args.role as string) : undefined;
        const name =
          typeof args.name === "string"
            ? (args.name as string)
            : typeof args.display_name === "string"
              ? (args.display_name as string)
              : typeof args.codename === "string"
                ? (args.codename as string)
                : (role ?? agentId);
        const task = compactSubagentTask(
          firstString(args, [
            "prompt_preview",
            "prompt",
            "goal",
            "task",
            "description",
            "query",
            "message",
          ]) ||
            (typeof args.summary === "string"
              ? (args.summary as string).slice(0, 100)
              : "") ||
            "",
        );

        let status: InlineSubagentStatus = hasToolResult ? "done" : "running";
        let summary =
          typeof args.summary === "string" ? (args.summary as string) : "";
        let error =
          typeof args.error === "string" ? (args.error as string) : "";
        let filesTouched = 0;
        let iterationCount: number | undefined;

        // Determine status from args.status (for "subagent" record items) or tool result.
        if (typeof args.status === "string") {
          const argStatus = (args.status as string).toLowerCase();
          if (
            argStatus === "done" ||
            argStatus === "completed" ||
            argStatus === "finished"
          ) {
            status = "done";
          } else if (argStatus === "error" || argStatus === "failed") {
            status = "error";
          } else if (argStatus === "running") {
            status = "running";
          } else if (
            argStatus === "waiting_approval" ||
            argStatus === "waiting"
          ) {
            status = "waiting";
          }
        }

        if (resultObj) {
          if (resultObj.ok === false || toolErrored) status = "error";
          else if (hasToolResult) status = "done";
          summary =
            firstString(resultObj, [
              "output",
              "result",
              "summary",
              "content",
            ]) || summary;
          error = firstString(resultObj, ["error", "message"]) || error;
          if (typeof resultObj.iteration_count === "number")
            iterationCount = resultObj.iteration_count;
          if (Array.isArray(resultObj.files_touched))
            filesTouched = resultObj.files_touched.length;
        } else if (resultIsString) {
          if (toolErrored) {
            status = "error";
            error = result as string;
          } else status = "done";
          summary = result as string;
        } else if (toolErrored) {
          status = "error";
        }

        if (Array.isArray(args.files_touched)) {
          filesTouched = Math.max(
            filesTouched,
            (args.files_touched as unknown[]).length,
          );
        }

        const resultAvatar =
          resultObj && typeof resultObj.avatar === "string"
            ? (resultObj.avatar as string)
            : undefined;
        const specAvatar =
          typeof args.avatar === "string" ? (args.avatar as string) : undefined;

        const progress =
          status === "done" ? 1.0 : status === "error" ? 0.5 : undefined;

        results.push({
          id: agentId,
          name,
          role: role !== name ? role : undefined,
          avatar: resultAvatar ?? specAvatar ?? roleEmoji(role ?? name),
          status,
          task,
          summary: summary || undefined,
          filesTouchedCount: filesTouched,
          iterationCount,
          error: error || undefined,
          index: agentIndex++,
          progress,
        });
      }
    }
  }
  // Dispatch specs, spawn markers, child tool rows and finish markers are
  // intentionally redundant transports. Fold all of them onto the requested
  // lane id before rendering so one worker never appears as 2-3 cards.
  const deduplicated = new Map<string, InlineSubagentInfo>();
  for (const candidate of results) {
    const existing = deduplicated.get(candidate.id);
    if (!existing) {
      deduplicated.set(candidate.id, candidate);
      continue;
    }
    const terminal = new Set<InlineSubagentStatus>(["done", "error"]);
    const status = terminal.has(candidate.status)
      ? candidate.status
      : terminal.has(existing.status)
        ? existing.status
        : candidate.status;
    deduplicated.set(candidate.id, {
      ...existing,
      name: candidate.name || existing.name,
      role: candidate.role ?? existing.role,
      avatar: candidate.avatar ?? existing.avatar,
      status,
      task: candidate.task || existing.task,
      summary: candidate.summary ?? existing.summary,
      filesTouchedCount: Math.max(
        existing.filesTouchedCount,
        candidate.filesTouchedCount,
      ),
      iterationCount: candidate.iterationCount ?? existing.iterationCount,
      error: candidate.error ?? existing.error,
      progress:
        status === "done" ? 1 : (candidate.progress ?? existing.progress),
    });
  }
  return Array.from(deduplicated.values()).map((agent, index) => ({
    ...agent,
    index,
  }));
}

function compactMission(value: string): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  const firstSentenceEnd = normalized.search(/[。！？!?]/);
  const sentence =
    firstSentenceEnd >= 0
      ? normalized.slice(0, firstSentenceEnd + 1)
      : normalized;
  return sentence.length > 140
    ? `${sentence.slice(0, 137).trimEnd()}…`
    : sentence;
}

/** Recover the shared mission for replayed clusters whose persisted lifecycle
 * markers contain identity/status but omit each worker's prompt. */
export function deriveSubagentMissionFromMessages(messages: Message[]): string {
  for (const message of messages) {
    if (message.type !== "ai") continue;
    for (const toolCall of (message as AIMessage).tool_calls ?? []) {
      const toolName = toolCall.name.toLowerCase();
      if (
        !isTeammateToolName(toolName) &&
        !toolName.includes("orchestrat") &&
        !toolName.includes("parallel")
      ) {
        continue;
      }
      const mission = firstString(
        (toolCall.args ?? {}) as Record<string, unknown>,
        ["goal", "objective", "mission", "task", "prompt"],
      );
      if (mission) return compactMission(mission);
    }
  }

  const humanPrompt = messages.find(
    (message) =>
      message.type === "human" && typeof message.content === "string",
  )?.content;
  return typeof humanPrompt === "string" ? compactMission(humanPrompt) : "";
}

/**
 * Two-row LED dot progress bar (2 rows × 14 columns), matching Kimi's style.
 * Columns light up left-to-right strictly by percentage:
 *   0%  - 6%   → 0 cols lit (just started)
 *   7%  - 13%  → 1 col lit
 *   ...
 *   93%+       → 13 cols lit (running cap — full 14 is the completed state)
 * The rightmost lit column pulses softly to show "actively working".
 */
function LedProgress({
  progress,
  completed = false,
}: {
  progress?: number;
  completed?: boolean;
}) {
  const cols = 14;
  // progress undefined/NaN → just started (0.1 = ~1 col)
  const p =
    typeof progress === "number" && !isNaN(progress)
      ? Math.max(0, Math.min(1, progress))
      : 0.1;
  // Running agents max out at 13/14; completion fills the matrix.
  const litCols = completed
    ? cols
    : Math.min(cols - 1, Math.floor(p * cols));
  const isActive = !completed && p > 0 && p < 1;

  return (
    <span
      className="inline-grid shrink-0 grid-flow-col grid-rows-2 gap-[1.5px] leading-none"
      aria-label={completed ? "completed" : "running"}
    >
      {Array.from({ length: cols * 2 }).map((_, index) => {
        const c = Math.floor(index / 2);
        const isLit = c < litCols;
        const isPulsing = isLit && c === litCols - 1 && isActive;
        return (
          <span
            key={index}
            className={cn(
              "inline-block rounded-sm transition-colors duration-base",
              isLit
                ? "bg-success/90 dark:bg-success/90"
                : "bg-success/15 dark:bg-success/15",
              isPulsing && "animate-[pulse-soft_1.2s_ease-in-out_infinite]",
            )}
            style={{
              width: "3px",
              height: "3px",
            }}
          />
        );
      })}
    </span>
  );
}

function StatusIndicator({
  status,
  progress,
}: {
  status: InlineSubagentStatus;
  progress?: number;
}) {
  if (status === "done") {
    return <LedProgress progress={1} completed />;
  }
  if (status === "error") {
    return (
      <span className="flex size-4 items-center justify-center text-destructive/80">
        <XCircleIcon className="size-3.5" />
      </span>
    );
  }
  if (status === "waiting") {
    return (
      <span className="flex size-4 items-center justify-center text-warning/80">
        <Loader2Icon className="size-3.5 animate-spin" />
      </span>
    );
  }
  // running: LED dot matrix with column-by-column fill
  return <LedProgress progress={progress} />;
}

function AgentIndexBadge({ index, done }: { index: number; done?: boolean }) {
  return (
    <span
      className={cn(
        "font-mono text-xs",
        done ? "text-muted-foreground/60" : "text-muted-foreground/60",
      )}
    >
      {String(index + 1).padStart(2, "0")}
    </span>
  );
}

// Subtle L-shaped tree connector like Kimi's — small, faint, minimal
function LConnector() {
  return (
    <span className="relative mr-1.5 mt-0.5 shrink-0 self-start">
      <span
        className="block w-px border-l border-muted-foreground/15"
        style={{ height: "8px" }}
      />
      <span className="absolute left-0 top-[8px] block h-px w-1 border-t border-muted-foreground/15" />
    </span>
  );
}

function isImageAvatar(avatar: string): boolean {
  return (
    avatar.startsWith("/") ||
    avatar.startsWith("http://") ||
    avatar.startsWith("https://") ||
    avatar.startsWith("data:") ||
    avatar.startsWith("blob:")
  );
}

function AgentAvatar({
  agent,
  large = false,
}: {
  agent: InlineSubagentInfo;
  large?: boolean;
}) {
  const className = large ? "size-8" : "size-4";
  if (agent.avatar && isImageAvatar(agent.avatar)) {
    return (
      <img
        alt=""
        src={agent.avatar}
        className={cn(className, "rounded-md object-cover")}
      />
    );
  }
  if (agent.avatar) {
    return (
      <span
        aria-hidden="true"
        className={cn(
          className,
          "flex items-center justify-center leading-none",
          large ? "text-xl" : "text-sm",
        )}
      >
        {agent.avatar}
      </span>
    );
  }
  return (
    <span
      className={cn(
        className,
        "flex items-center justify-center rounded-md bg-muted text-muted-foreground/70",
      )}
    >
      <BotIcon className={large ? "size-4" : "size-[13px]"} />
    </span>
  );
}

function KimiStyleSubagentCard({
  agent,
  previewEnabled,
  turnIndex,
}: {
  agent: InlineSubagentInfo;
  previewEnabled: boolean;
  turnIndex?: number;
}) {
  const { t } = useI18n();
  const detailId = `${useId()}-agent-preview`;
  const [previewSuppressed, setPreviewSuppressed] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const hasReport = Boolean(agent.summary || agent.error);
  const isRunning = agent.status === "running" || agent.status === "waiting";
  const isDone = agent.status === "done";
  const statusLabel =
    agent.status === "done"
      ? t.message.statusCompleted
      : agent.status === "error"
        ? t.message.statusError
        : agent.status === "waiting"
          ? t.message.statusWaiting
          : t.message.statusViewing;
  const handleClick = (event: React.MouseEvent<HTMLButtonElement>) => {
    setPreviewSuppressed(true);
    event.currentTarget.blur();
    // Open the agent in the workbench
    emitAgentWorkbenchFocus({
      agentId: agent.id,
      agent: {
        id: agent.id,
        name: agent.name,
        role: agent.role,
        avatar: agent.avatar,
        status: agent.status,
        task: agent.task,
        summary: agent.summary,
        iterationCount: agent.iterationCount,
        filesTouchedCount: agent.filesTouchedCount,
        error: agent.error,
        index: agent.index,
      },
      turnIndex,
      tab: "agent",
      view: "screen",
    });
  };

  return (
    <div
      className="group/agent-card relative mb-1 last:mb-0"
      onMouseLeave={() => setPreviewSuppressed(false)}
    >
      <button
        type="button"
        onClick={handleClick}
        aria-label={`${agent.name} · ${agent.role ?? t.message.agent} · ${agent.task || t.message.noTaskDescription} · ${statusLabel}`}
        aria-describedby={detailId}
        className={cn(
          "group/agent-row flex w-full items-start gap-0 rounded-md bg-background/55 px-2.5 py-1.5 text-left transition-colors",
          "hover:bg-background/80 focus-visible:bg-background/80 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/50 dark:bg-background/25 dark:hover:bg-background/40",
        )}
      >
        {/* Avatar - compact, matches Kimi's small avatar style */}
        <span className="mr-1.5 mt-px shrink-0">
          <AgentAvatar agent={agent} />
        </span>

        {/* Content area - two rows */}
        <div className="min-w-0 flex-1">
          {/* Row 1: Name + index */}
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "truncate text-sm leading-tight",
                isRunning
                  ? "text-foreground"
                  : isDone
                    ? "text-foreground/70"
                    : "text-foreground/80",
              )}
            >
              {agent.name}
            </span>

            <span className="flex-1" />

            {!agent.task && (
              <span className="mr-1.5 shrink-0">
                <StatusIndicator
                  status={agent.status}
                  progress={agent.progress}
                />
              </span>
            )}

            <AgentIndexBadge index={agent.index ?? 0} done={isDone} />
          </div>

          {/* Row 2: L-connector + task text + status indicator */}
          {agent.task && (
            <div className="mt-px flex items-center gap-0">
              <LConnector />
              <span
                className={cn(
                  "min-w-0 flex-1 truncate text-xs leading-snug",
                  isDone
                    ? "text-muted-foreground/60"
                    : "text-muted-foreground/70",
                )}
              >
                {agent.task}
              </span>
              {(agent.iterationCount !== undefined ||
                agent.filesTouchedCount > 0) && (
                <span
                  data-testid={`agent-card-stats-${agent.index ?? 0}`}
                  className="ml-2 flex shrink-0 items-center gap-1 text-micro tabular-nums text-muted-foreground/65"
                >
                  {agent.iterationCount !== undefined && (
                    <span>
                      {agent.iterationCount} {t.subagents.iterations}
                    </span>
                  )}
                  {agent.iterationCount !== undefined &&
                    agent.filesTouchedCount > 0 && (
                      <span aria-hidden="true">·</span>
                    )}
                  {agent.filesTouchedCount > 0 && (
                    <span>
                      {agent.filesTouchedCount} {t.subagents.filesModified}
                    </span>
                  )}
                </span>
              )}
              <span className="ml-1.5 shrink-0 self-center">
                <StatusIndicator
                  status={agent.status}
                  progress={agent.progress}
                />
              </span>
            </div>
          )}
        </div>
      </button>

      {hasReport && (
        <div className="pl-8 pr-1">
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              setPreviewSuppressed(true);
              event.currentTarget.blur();
              setReportOpen((value) => !value);
            }}
            aria-expanded={reportOpen}
            className={cn(
              "mt-0.5 inline-flex items-center gap-1 rounded px-1 py-0.5 text-micro font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/50",
              reportOpen
                ? "text-foreground/75 hover:bg-background/60"
                : "text-muted-foreground/70 hover:bg-background/60 hover:text-foreground",
            )}
          >
            {reportOpen ? (
              <ChevronUpIcon className="size-3" />
            ) : (
              <ChevronDownIcon className="size-3" />
            )}
            {reportOpen
              ? t.message.collapseReport
              : agent.error
                ? t.message.viewReportError
                : t.message.viewReport}
          </button>
          {reportOpen && (
            <div
              data-testid={`agent-report-${agent.index ?? 0}`}
              className="mt-1 max-h-72 overflow-y-auto whitespace-pre-wrap rounded-md border border-border/50 bg-background/40 px-2.5 py-2 text-xs leading-relaxed text-muted-foreground"
            >
              {agent.error ? (
                <span className="text-destructive/80">{agent.error}</span>
              ) : (
                agent.summary
              )}
            </div>
          )}
        </div>
      )}

      {previewEnabled && !previewSuppressed && (
        <div
          id={detailId}
          role="tooltip"
          data-testid={`agent-hover-${agent.index ?? 0}`}
          className="pointer-events-none invisible absolute left-5 top-full z-40 w-[min(28rem,calc(100vw-4rem))] pt-1.5 opacity-0 transition-opacity duration-150 group-hover/agent-card:visible group-hover/agent-card:opacity-100 group-hover/agent-card:delay-500 group-focus-within/agent-card:visible group-focus-within/agent-card:opacity-100 group-focus-within/agent-card:delay-0"
        >
          <div className="rounded-lg border border-border/70 bg-popover p-3 text-popover-foreground shadow-lg">
            <div className="flex items-start gap-2.5">
              <AgentAvatar agent={agent} large />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">
                    {agent.name}
                  </span>
                  <AgentIndexBadge index={agent.index ?? 0} done={isDone} />
                  <span className="ml-auto flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                    <StatusIndicator
                      status={agent.status}
                      progress={agent.progress}
                    />
                    {statusLabel}
                  </span>
                </div>
                {agent.role && agent.role !== agent.name && (
                  <div className="mt-0.5 truncate text-xs text-muted-foreground">
                    {agent.role}
                  </div>
                )}
              </div>
            </div>

            <div className="mt-2 text-xs leading-relaxed text-foreground/85">
              {agent.task || t.message.noTaskDescription}
            </div>

            {(agent.iterationCount !== undefined ||
              agent.filesTouchedCount > 0) && (
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-border/60 pt-2 text-xs text-muted-foreground">
                {agent.iterationCount !== undefined && (
                  <span>
                    {agent.iterationCount} {t.subagents.iterations}
                  </span>
                )}
                {agent.filesTouchedCount > 0 && (
                  <span>
                    {agent.filesTouchedCount} {t.subagents.filesModified}
                  </span>
                )}
              </div>
            )}

            {agent.summary && agent.status !== "error" && (
              <div className="mt-2 line-clamp-3 text-xs leading-relaxed text-muted-foreground">
                {agent.summary}
              </div>
            )}
            {agent.error && agent.status === "error" && (
              <div className="mt-2 line-clamp-3 text-xs leading-relaxed text-destructive/80">
                {agent.error}
              </div>
            )}

            <div className="mt-2 flex items-center gap-1 border-t border-border/60 pt-2 text-xs font-medium text-foreground/75">
              <MonitorIcon className="size-3.5" />
              {t.message.viewComputer}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function InlineSubagentCards({
  events,
  agents: preDerivedAgents,
  mission,
  settled = false,
  turnIndex,
  className,
}: {
  events?: LiveToolEvent[];
  agents?: InlineSubagentInfo[];
  mission?: string;
  settled?: boolean;
  turnIndex?: number;
  className?: string;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const [previewEnabled, setPreviewEnabled] = useState(true);
  const eventAgents = useMemo(
    () =>
      (events ? deriveInlineSubagents(events) : []).map((agent) =>
        agent.task || !mission ? agent : { ...agent, task: mission },
      ),
    [events, mission],
  );
  const preparedAgents = useMemo(
    () =>
      preDerivedAgents?.map((agent) =>
        agent.task || !mission ? agent : { ...agent, task: mission },
      ),
    [mission, preDerivedAgents],
  );

  const mergedAgents = useMemo(() => {
    // Step 1: Normalize preDerivedAgents — collapse anonymous runtime duplicates into named spec entries.
    // deriveSubagentsFromMessages() may produce two groups:
    //   (a) "named" entries from the batch `specs`/`agents`/`tasks` array (e.g. calculator/translator/analyst)
    //   (b) "anonymous/runtime" entries from subsequent per-agent delegate/spawn calls, which carry the
    //       real task text but have generic names like "general"/UUID. These are duplicates of (a) that
    //       hold the task payload — they must be MERGED INTO the named entries, not rendered separately.
    const GENERIC_NAMES = new Set([
      "general",
      "agent",
      "subagent",
      "worker",
      "assistant",
    ]);
    const isAnonymousRuntime = (a: InlineSubagentInfo): boolean => {
      if (!a.name) return true;
      const lower = a.name.toLowerCase();
      if (GENERIC_NAMES.has(lower)) return Boolean(a.task);
      if (
        lower.startsWith("subagent_spawn_") ||
        lower.startsWith("subagent-spawn-") ||
        lower.startsWith("runtime_subagent_")
      ) {
        return true;
      }
      if (a.id && a.id === a.name && /^[a-f0-9-]{8,}$/i.test(a.id))
        return Boolean(a.task);
      return false;
    };

    /** Merge src fields into dst (dst wins on name/avatar/role; src wins on task/progress/summary). */
    const mergeInto = (
      dst: InlineSubagentInfo,
      src: InlineSubagentInfo,
    ): InlineSubagentInfo => {
      // Live terminal state wins. A non-terminal live marker must not regress
      // a terminal result already present in the persisted message stream.
      const terminal = new Set<InlineSubagentStatus>(["done", "error"]);
      const status: InlineSubagentStatus = terminal.has(src.status)
        ? src.status
        : terminal.has(dst.status)
          ? dst.status
          : src.status || dst.status;
      let progress: number | undefined;
      if (status === "done") progress = 1.0;
      else if (status === "error")
        progress = src.progress ?? dst.progress ?? 0.5;
      else progress = src.progress ?? dst.progress;
      return {
        ...dst,
        status,
        progress,
        task: dst.task || src.task,
        summary: dst.summary || src.summary,
        error: dst.error || src.error,
        filesTouchedCount: Math.max(
          dst.filesTouchedCount,
          src.filesTouchedCount,
        ),
        iterationCount: dst.iterationCount ?? src.iterationCount,
      };
    };

    // Build a cleaned, deduplicated base list from preDerivedAgents.
    let cleanedPres: InlineSubagentInfo[] = [];
    if (preparedAgents && preparedAgents.length > 0) {
      const namedPres: InlineSubagentInfo[] = [];
      const anonPres: InlineSubagentInfo[] = [];
      for (const a of preparedAgents) {
        if (isAnonymousRuntime(a)) anonPres.push(a);
        else namedPres.push(a);
      }

      // If anon count ≤ named count, fold them in by position (spawn order == spec order).
      // If anon count > named count (rare), fold as many as possible then append the rest.
      const mergedNamed = namedPres.map((n, i) =>
        anonPres[i] ? mergeInto(n, anonPres[i]) : n,
      );
      const unmatchedRuntime = anonPres
        .slice(namedPres.length)
        .filter((agent) => Boolean(agent.task))
        .map((agent, index) => ({
          ...agent,
          name: `子智能体 ${String(mergedNamed.length + index + 1).padStart(2, "0")}`,
        }));
      cleanedPres = [...mergedNamed, ...unmatchedRuntime];
    }

    // Step 2: Merge with live event agents.
    if (cleanedPres.length === 0) return eventAgents;
    if (eventAgents.length === 0) {
      // Re-index on the way out so badge numbers are 1..N.
      return cleanedPres.map((a, i) => ({ ...a, index: i }));
    }

    const preById = new Map(cleanedPres.map((a) => [a.id, a]));
    const preByName = new Map<string, InlineSubagentInfo>();
    for (const a of cleanedPres) {
      if (a.name) preByName.set(a.name.toLowerCase(), a);
      if (a.role) preByName.set(a.role.toLowerCase(), a);
    }
    const merged: InlineSubagentInfo[] = [];
    const seenPreIds = new Set<string>();

    // Match human-facing lifecycle records first. Runtime spawn markers often
    // arrive earlier in the stream; processing them first consumed positional
    // matches and left the real codenames as duplicate rows.
    const orderedEventAgents = [
      ...eventAgents.filter((agent) => !isAnonymousRuntime(agent)),
      ...eventAgents.filter(isAnonymousRuntime),
    ];

    for (let i = 0; i < orderedEventAgents.length; i++) {
      const ea = orderedEventAgents[i]!;
      let pre = preById.get(ea.id);
      if (!pre && ea.name) pre = preByName.get(ea.name.toLowerCase());
      if (!pre && ea.role) pre = preByName.get(ea.role.toLowerCase());
      // Positional fallback is only for anonymous runtime markers. Named
      // records must never steal another named agent's slot.
      if (!pre && isAnonymousRuntime(ea)) {
        pre = cleanedPres.find((candidate) => !seenPreIds.has(candidate.id));
      }

      if (pre) {
        seenPreIds.add(pre.id);
        const combined = mergeInto(pre, ea);
        // Keep pre's stable identity (id/name/avatar) but let ea's live data win for progress/status/task
        // when ea's status is more recent (done/error overrides running, but not vice-versa).
        const finalStatus: InlineSubagentStatus = combined.status;
        let finalProgress: number | undefined;
        if (finalStatus === "done") finalProgress = 1.0;
        else if (finalStatus === "error")
          finalProgress = ea.progress ?? pre.progress ?? 0.5;
        else finalProgress = ea.progress ?? pre.progress;

        merged.push({
          ...combined,
          id: pre.id,
          name:
            pre.name && pre.name !== pre.id ? pre.name : ea.name || pre.name,
          role: pre.role || ea.role,
          avatar: pre.avatar ?? ea.avatar,
          index: i,
          task: ea.task || pre.task,
          summary: ea.summary || pre.summary,
          status: finalStatus,
          progress: finalProgress,
        });
      } else {
        // A bare spawn marker without task/status detail is only transport
        // bookkeeping. A matching named lifecycle record already represents
        // it, so exposing the raw subagent_spawn_* id adds noise and inflates
        // the visible task count.
        if (isAnonymousRuntime(ea) && !ea.task && cleanedPres.length > 0) {
          continue;
        }
        merged.push({
          ...ea,
          name: isAnonymousRuntime(ea)
            ? `子智能体 ${String(merged.length + 1).padStart(2, "0")}`
            : ea.name,
          index: merged.length,
        });
      }
    }
    for (const pa of cleanedPres) {
      if (!seenPreIds.has(pa.id)) merged.push(pa);
    }
    return merged.map((a, i) => ({ ...a, index: i }));
  }, [eventAgents, preparedAgents]);
  const agents = useMemo(() => {
    if (!settled) return mergedAgents;
    return mergedAgents.map((agent) => {
      if (agent.status === "done" || agent.status === "error") return agent;
      const summary = agent.summary?.trim() ?? "";
      const failed =
        Boolean(agent.error) ||
        /^(?:connecterror|error\b|failed\b|failure\b|exception\b)|\b(?:exceeded round cap|timed out|connection failed)\b|^(?:失败|错误|超时|中断)/i.test(
          summary,
        );
      return {
        ...agent,
        status: failed ? ("error" as const) : ("done" as const),
        progress: failed ? (agent.progress ?? 0.5) : 1,
      };
    });
  }, [mergedAgents, settled]);

  if (agents.length === 0) return null;

  const runningCount = agents.filter(
    (a) => a.status === "running" || a.status === "waiting",
  ).length;
  const errorCount = agents.filter((a) => a.status === "error").length;
  const doneCount = agents.filter((a) => a.status === "done").length;
  const hiddenCount = Math.max(0, agents.length - MAX_COLLAPSED_AGENTS);
  const visibleAgents = expanded
    ? agents
    : agents.slice(0, MAX_COLLAPSED_AGENTS);

  return (
    <div className={cn("my-1.5", className)}>
      {/* Kimi-style container: header + agents in one light card */}
      <div className="rounded-md bg-muted/30 dark:bg-muted/15 px-1 py-1">
        {/* Header inside the card */}
        <div className="flex items-center gap-1.5 px-2 py-0.5">
          <UsersIcon className="size-[13px] text-muted-foreground/60" />
          <span className="text-xs text-muted-foreground/70">
            {t.message.agentCluster}
          </span>
          <span className="text-xs text-muted-foreground/40">|</span>
          <span className="text-xs text-muted-foreground/60">
            {t.message.agentProgressSummary(
              agents.length,
              doneCount,
              errorCount,
            )}
            {runningCount > 0
              ? ` · ${runningCount} ${t.subagents.running}`
              : ""}
          </span>
        </div>

        {/* Agent rows */}
        <div
          className="mt-0.5"
          onPointerMove={() => {
            if (!previewEnabled) setPreviewEnabled(true);
          }}
          onFocusCapture={() => {
            if (!previewEnabled) setPreviewEnabled(true);
          }}
        >
          {visibleAgents.map((agent) => (
            <KimiStyleSubagentCard
              key={agent.id}
              agent={agent}
              previewEnabled={previewEnabled}
              turnIndex={turnIndex}
            />
          ))}
          {hiddenCount > 0 && (
            <button
              type="button"
              onClick={() => {
                setPreviewEnabled(false);
                setExpanded((value) => !value);
              }}
              className="mt-0.5 flex w-full items-center justify-center gap-1 rounded border border-dashed border-border/60 px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-background/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/50"
            >
              {expanded ? (
                <ChevronUpIcon className="size-3.5" />
              ) : (
                <ChevronDownIcon className="size-3.5" />
              )}
              {expanded
                ? t.message.collapseAgents
                : t.message.showMoreAgents(hiddenCount)}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
