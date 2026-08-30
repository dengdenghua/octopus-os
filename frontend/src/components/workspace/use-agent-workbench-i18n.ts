import { useI18n } from "@/core/i18n/hooks";
import type { AgentPhase } from "./agent-phases";
import type { LiveToolEvent } from "./live-tool-timeline";
import type { WorkBlock } from "./work-blocks";
import {
  agentRunBadgeClass,
  agentRunDotClass,
  agentRunTextClass,
} from "./agent-run-status";
import {
  type AgentTile,
  avatarForRole,
  agentEventGroupId,
  readableToolName,
  thoughtFromEvent,
  blackboardWritesFromEvent,
  filesFromAgentEvent,
  errorFromAgentEvent,
  isRecord,
  stringFromKeys,
  textFromUnknown,
  compactDetail,
  uniqueStrings,
  maxDefined,
  isInternalAutoParallelFailure,
} from "./agent-workbench-utils";

export function deriveAgentTilesFromEvents(
  events: LiveToolEvent[],
): AgentTile[] {
  const byId = new Map<string, AgentTile>();
  const ordered = [...events].sort((a, b) => a.startedAt - b.startedAt);
  for (const event of ordered) {
    if (isInternalAutoParallelFailure(event)) continue;
    addDispatchSpecTiles(event, byId);
    addDispatchResultTiles(event, byId);
    const id = agentEventGroupId(event);
    if (!id || id === "__main__") continue;
    const existing = byId.get(id);
    // Only an agent lifecycle/result marker may settle an agent. Child tool
    // rows also use done/error for their OWN execution state; treating those
    // as the worker's terminal state made cards claim completion while the
    // child was still running more rounds.
    const isTerminalAgentEvent =
      event.lifecycle === "finished" ||
      (event.name === "subagent" &&
        event.lifecycle === undefined &&
        (event.status === "done" || event.status === "error"));
    let status: AgentTile["status"];
    if (event.lifecycle === "spawned") {
      status = "running";
    } else if (isTerminalAgentEvent) {
      status = event.status === "error" ? "error" : "done";
    } else if (event.status === "waiting_approval") {
      status = "waiting_approval";
    } else if (existing || event.status === "running" || event.status === "done") {
      status = "running";
    } else {
      status = "pending";
    }
    const existingIsTerminal =
      existing?.status === "done" || existing?.status === "error";
    // Stale child activity cannot reopen a lifecycle-complete tile. Likewise,
    // don't downgrade running to pending when a sparse event arrives.
    const finalStatus = existingIsTerminal
      ? existing.status
      : existing?.status === "running" && status === "pending"
        ? existing.status
        : status;
    const currentTool =
      event.lifecycle === undefined
        ? readableToolName(event)
        : existing?.currentTool;
    const lastThought = thoughtFromEvent(event) ?? existing?.lastThought;
    const prompt =
      stringFromKeys(event.input, [
        "prompt",
        "prompt_preview",
        "task",
        "description",
        "query",
        "objective",
      ]) ?? existing?.prompt;
    const resultSummary = existing?.resultSummary;
    const blackboardWrites = uniqueStrings([
      ...(existing?.blackboardWrites ?? []),
      ...blackboardWritesFromEvent(event),
    ]);
    const filesTouched = uniqueStrings([
      ...(existing?.filesTouched ?? []),
      ...filesFromAgentEvent(event),
    ]);
    const startedAt = Math.min(
      existing?.startedAt ?? event.startedAt,
      event.startedAt,
    );
    byId.set(id, {
      id,
      name:
        event.subagentCodename ??
        existing?.codename ??
        event.agentName ??
        event.subAgentRole ??
        id,
      label: existing?.label ?? String(byId.size + 1).padStart(2, "0"),
      status: finalStatus,
      task:
        lastThought ?? existing?.task ?? currentTool ?? readableToolName(event),
      prompt,
      avatar:
        event.subagentAvatar ??
        existing?.avatar ??
        avatarForRole(event.subAgentRole),
      codename: event.subagentCodename ?? existing?.codename,
      role: event.subAgentRole ?? existing?.role,
      roleDisplayName:
        event.subagentRoleDisplayName ?? existing?.roleDisplayName,
      roleDescription:
        event.subagentRoleDescription ?? existing?.roleDescription,
      specIndex: existing?.specIndex,
      taskLabel: existing?.taskLabel,
      parentToolUseId: event.parentToolUseId ?? existing?.parentToolUseId,
      currentTool,
      lastThought,
      resultSummary,
      iterationCount: event.iterationCount ?? existing?.iterationCount,
      blackboardWrites,
      filesTouched,
      durationMs: event.durationMs ?? existing?.durationMs,
      error:
        isTerminalAgentEvent && status === "error"
          ? (errorFromAgentEvent(event) ?? existing?.error)
          : isTerminalAgentEvent
            ? undefined
            : existing?.error,
      eventCount: (existing?.eventCount ?? 0) + 1,
      startedAt,
      finishedAt: maxDefined(existing?.finishedAt, event.finishedAt),
    });
  }
  if (byId.size > 0) return Array.from(byId.values()).slice(0, 12);

  // No dispatch specs or real sub-agent lifecycle events were observed.
  return [];
}

export function useAgentWorkbenchI18n() {
  const { t } = useI18n();

  function phaseBlockSummary(phase: AgentPhase, blocks: WorkBlock[]) {
    const related = blocks.filter((block) => phase.blockIds.includes(block.id));
    if (related.length === 0) {
      if (phase.status === "done") return t.agentWorkbench.phaseCompleted;
      if (phase.status === "error") return t.agentWorkbench.statusError;
      return t.agentWorkbench.waitingForPhase;
    }
    const searches = related.filter(
      (block) => block.kind === "search" || block.kind === "browser",
    ).length;
    const files = related.filter(
      (block) => block.kind === "file" || block.kind === "read",
    ).length;
    const terminals = related.filter(
      (block) => block.kind === "terminal",
    ).length;
    const parts = [
      searches ? t.agentWorkbench.webSearchActions(searches) : "",
      files ? t.agentWorkbench.fileActions(files) : "",
      terminals ? t.agentWorkbench.terminalActions(terminals) : "",
    ].filter(Boolean);
    return parts.length > 0
      ? parts.join(t.agentWorkbench.listSeparator)
      : t.agentWorkbench.executionActions(related.length);
  }

  function agentStatusLabel(status: AgentTile["status"]) {
    if (status === "running") return t.agentWorkbench.statusProcessing;
    if (status === "waiting_approval")
      return t.agentWorkbenchPages.statusWaitingApproval;
    if (status === "done") return t.agentWorkbench.statusCompleted;
    if (status === "error") return t.agentWorkbench.statusError;
    return t.agentWorkbench.waitingToStart;
  }

  function agentStatusClass(status: AgentTile["status"]) {
    return agentRunTextClass(status);
  }

  function workbenchStatus(
    blocks: WorkBlock[],
    phases: AgentPhase[],
    options?: {
      settled?: boolean;
      failed?: boolean;
      interrupted?: boolean;
      blocked?: boolean;
    },
  ) {
    // The server can leave a stale pending phase in a replayed snapshot even
    // after the turn has emitted its final answer. Once the host tells us the
    // run is settled, that lifecycle signal is authoritative for the compact
    // header badge; the detailed phase list remains available below.
    if (options?.failed) {
      return {
        label: t.agentWorkbench.statusError,
        className: agentRunBadgeClass("error"),
        dotClassName: agentRunDotClass("error"),
      };
    }
    if (options?.interrupted) {
      return {
        label: t.backgroundTasks.interrupted,
        className: agentRunBadgeClass("waiting"),
        dotClassName: agentRunDotClass("waiting"),
      };
    }
    if (options?.blocked) {
      return {
        label: t.streaming.blockedOnUser,
        className: agentRunBadgeClass("waiting"),
        dotClassName: agentRunDotClass("waiting"),
      };
    }
    if (options?.settled) {
      return {
        label: t.agentWorkbench.statusCompleted,
        className: agentRunBadgeClass("done"),
        dotClassName: agentRunDotClass("done"),
      };
    }
    if (blocks.length === 0 && phases.length === 0) {
      return {
        label: t.agentWorkbenchPanel.agentStatusPending,
        className: agentRunBadgeClass("pending"),
        dotClassName: agentRunDotClass("pending"),
      };
    }
    if (
      phases.some((phase) => phase.status === "error") ||
      blocks.some((block) => block.status === "error")
    ) {
      return {
        label: t.agentWorkbench.statusError,
        className: agentRunBadgeClass("error"),
        dotClassName: agentRunDotClass("error"),
      };
    }
    if (
      phases.some((phase) => phase.status === "waiting_approval") ||
      blocks.some((block) => block.status === "waiting_approval")
    ) {
      return {
        label: t.agentWorkbenchPages.statusWaitingApproval,
        className: agentRunBadgeClass("waiting"),
        dotClassName: agentRunDotClass("waiting"),
      };
    }
    if (phases.some((phase) => phase.status === "running")) {
      return {
        label: t.agentWorkbench.executingTask,
        className: agentRunBadgeClass("running"),
        dotClassName: agentRunDotClass("running"),
      };
    }
    if (phases.some((phase) => phase.status === "pending")) {
      return {
        label: t.agentWorkbench.waitingToContinue,
        className: agentRunBadgeClass("pending"),
        dotClassName: agentRunDotClass("pending"),
      };
    }
    return {
      label: t.agentWorkbench.statusCompleted,
      className: agentRunBadgeClass("done"),
      dotClassName: agentRunDotClass("done"),
    };
  }

  return {
    deriveAgentTiles: deriveAgentTilesFromEvents,
    phaseBlockSummary,
    agentStatusLabel,
    agentStatusClass,
    workbenchStatus,
  };
}

function addDispatchSpecTiles(
  event: LiveToolEvent,
  byId: Map<string, AgentTile>,
) {
  if (
    event.name !== "call_agent_parallel" &&
    event.name !== "call_agent" &&
    event.name !== "team_swarm"
  ) {
    return;
  }
  if (dispatchRejectedBeforeSpawn(event)) return;
  const specs = dispatchSpecsFromEvent(event);
  specs.forEach((spec, index) => {
    const requestedId = stringFromKeys(spec, ["agent_id", "subagent_id"]);
    const role =
      (requestedId ??
        stringFromKeys(spec, [
          "subagent_name",
          "subagent_type",
          "role",
          "name",
          "agent",
        ])) ||
      `agent-${index + 1}`;
    const taskLabel =
      stringFromKeys(spec, ["task_label", "bb_key", "key", "lane", "title"]) ||
      role;
    const id =
      event.name === "team_swarm"
        ? role
        : event.name === "call_agent_parallel"
          ? (requestedId ?? `${event.id}:spec:${index + 1}`)
          : event.id;
    const existing = byId.get(id);
    const prompt =
      stringFromKeys(spec, [
        "prompt",
        "task",
        "description",
        "query",
        "objective",
      ]) ||
      existing?.prompt ||
      existing?.task ||
      readableToolName(event);

    byId.set(id, {
      id,
      name: existing?.name ?? taskLabel,
      label: existing?.label ?? String(byId.size + 1).padStart(2, "0"),
      status:
        existing && existing.eventCount > 0
          ? existing.status
          : dispatchStatus(event.status),
      task: existing?.task ?? prompt,
      prompt,
      avatar: existing?.avatar ?? avatarForRole(role),
      codename: existing?.codename,
      role: existing?.role ?? role,
      specIndex: existing?.specIndex ?? index,
      taskLabel: existing?.taskLabel ?? taskLabel,
      parentToolUseId: event.id,
      currentTool: existing?.currentTool,
      lastThought: existing?.lastThought,
      resultSummary: existing?.resultSummary,
      iterationCount: existing?.iterationCount,
      blackboardWrites: existing?.blackboardWrites ?? [],
      filesTouched: existing?.filesTouched ?? [],
      durationMs: existing?.durationMs,
      error: existing?.error,
      eventCount: existing?.eventCount ?? 0,
      startedAt: existing?.startedAt ?? event.startedAt,
      finishedAt:
        existing?.finishedAt ??
        (event.status === "done" || event.status === "error"
          ? (event.finishedAt ?? event.startedAt)
          : undefined),
    });
  });
}

type DispatchResultRecord = Record<string, unknown> & {
  agent_id?: unknown;
  role?: unknown;
  output?: unknown;
  partial_output?: unknown;
  error?: unknown;
  error_type?: unknown;
  spec_index?: unknown;
  task_label?: unknown;
  bb_key?: unknown;
  task_preview?: unknown;
  files_touched?: unknown;
  duration_s?: unknown;
  iteration_count?: unknown;
  rounds_completed?: unknown;
  round_cap_exceeded?: unknown;
};

function addDispatchResultTiles(
  event: LiveToolEvent,
  byId: Map<string, AgentTile>,
) {
  if (
    event.name !== "call_agent_parallel" &&
    event.name !== "call_agent" &&
    event.name !== "team_swarm"
  ) {
    return;
  }
  if (event.status !== "done" && event.status !== "error") return;
  const envelope = dispatchEnvelopeFromOutput(event.output);
  if (!envelope) return;

  const successes = Array.isArray(envelope.successes)
    ? envelope.successes.filter(isRecord)
    : [];
  const failures = Array.isArray(envelope.failures)
    ? envelope.failures.filter(isRecord)
    : [];

  if (successes.length === 0 && failures.length === 0) {
    const rawResults = Array.isArray(envelope.results)
      ? envelope.results.filter(isRecord)
      : [];
    rawResults.forEach((result, index) => {
      mergeDispatchResultTile(event, byId, result, index);
    });
    return;
  }

  [...successes, ...failures].forEach((result, index) => {
    mergeDispatchResultTile(event, byId, result, index);
  });
}

function dispatchEnvelopeFromOutput(
  output: unknown,
): Record<string, unknown> | null {
  if (isRecord(output)) return output;
  if (typeof output !== "string") return null;
  const trimmed = output.trim();
  if (!trimmed) return null;
  const candidates = [trimmed];
  const firstBrace = trimmed.indexOf("{");
  const lastBrace = trimmed.lastIndexOf("}");
  if (firstBrace >= 0 && lastBrace > firstBrace) {
    candidates.push(trimmed.slice(firstBrace, lastBrace + 1));
  }
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (isRecord(parsed)) return parsed;
    } catch {
      // Try the next candidate.
    }
  }
  return null;
}

function mergeDispatchResultTile(
  event: LiveToolEvent,
  byId: Map<string, AgentTile>,
  result: DispatchResultRecord,
  index: number,
) {
  const role =
    stringFromKeys(result, [
      "agent_id",
      "role",
      "custom_role",
      "resolved_to",
    ]) || `agent-${index + 1}`;
  const taskLabel =
    stringFromKeys(result, ["task_label", "bb_key", "lane", "title"]) || role;
  const id = idForDispatchResult(event, byId, role, result, index);
  const existing = byId.get(id);
  const resolvedTaskLabel =
    taskLabel && taskLabel !== role ? taskLabel : (existing?.taskLabel ?? role);
  const error = stringFromKeys(result, ["error", "message"]);
  const output =
    stringFromKeys(result, ["output", "partial_output", "summary", "result"]) ||
    textFromUnknown(result.output ?? result.partial_output);
  const resultSummary = output
    ? compactDetail(output, 260)
    : existing?.resultSummary;
  const filesTouched = uniqueStrings([
    ...(existing?.filesTouched ?? []),
    ...arrayOfStrings(result.files_touched),
  ]);
  const iterationCount = numberFromUnknown(
    result.iteration_count ?? result.rounds_completed,
  );
  const durationSeconds = numberFromUnknown(result.duration_s);
  const status: AgentTile["status"] = error
    ? "error"
    : event.status === "error"
      ? "error"
      : "done";
  const suffixes = [
    iterationCount !== undefined ? `${iterationCount} 轮` : "",
    result.round_cap_exceeded ? "轮次上限" : "",
  ].filter(Boolean);
  const lastThought =
    resultSummary ??
    (error
      ? `失败：${compactDetail(error, 180)}`
      : suffixes.length > 0
        ? suffixes.join(" · ")
        : existing?.lastThought);

  byId.set(id, {
    id,
    name: resolvedTaskLabel,
    label: existing?.label ?? String(byId.size + 1).padStart(2, "0"),
    status,
    task: existing?.task ?? resultSummary ?? readableToolName(event),
    prompt: existing?.prompt,
    avatar: existing?.avatar ?? avatarForRole(role),
    codename: existing?.codename,
    role: existing?.role ?? role,
    specIndex: numberFromUnknown(result.spec_index) ?? existing?.specIndex,
    taskLabel: resolvedTaskLabel,
    parentToolUseId: existing?.parentToolUseId ?? event.id,
    currentTool: existing?.currentTool,
    lastThought,
    resultSummary,
    iterationCount: iterationCount ?? existing?.iterationCount,
    blackboardWrites: existing?.blackboardWrites ?? [],
    filesTouched,
    durationMs:
      durationSeconds !== undefined
        ? durationSeconds * 1000
        : (existing?.durationMs ?? event.durationMs),
    error: error || existing?.error,
    eventCount: Math.max(existing?.eventCount ?? 0, 1),
    startedAt: existing?.startedAt ?? event.startedAt,
    finishedAt: event.finishedAt ?? existing?.finishedAt ?? event.startedAt,
  });
}

function idForDispatchResult(
  event: LiveToolEvent,
  byId: Map<string, AgentTile>,
  role: string,
  result: DispatchResultRecord,
  index: number,
): string {
  if (event.name === "call_agent") return event.id;
  const requestedId = stringFromKeys(result, ["agent_id"]);
  if (requestedId && byId.has(requestedId)) return requestedId;
  const specIndex = numberFromUnknown(result.spec_index);
  if (specIndex !== undefined) {
    const specId = `${event.id}:spec:${specIndex + 1}`;
    if (byId.has(specId)) return specId;
  }
  const base = `${event.id}:${role}`;
  if (isOpenDispatchResultSlot(byId.get(base))) {
    return base;
  }
  for (let occurrence = 2; occurrence <= index + 2; occurrence += 1) {
    const candidate = `${base}:${occurrence}`;
    if (isOpenDispatchResultSlot(byId.get(candidate))) {
      return candidate;
    }
  }
  return byId.has(base) ? `${base}:result-${index + 1}` : base;
}

function isOpenDispatchResultSlot(tile: AgentTile | undefined): boolean {
  if (!tile) return false;
  return !tile.resultSummary && !tile.error;
}

function arrayOfStrings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is string =>
          typeof item === "string" && item.trim().length > 0,
      )
    : [];
}

function numberFromUnknown(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function dispatchSpecsFromEvent(
  event: LiveToolEvent,
): Record<string, unknown>[] {
  if (event.name === "call_agent") {
    return event.input ? [event.input] : [];
  }
  const specs = event.input?.specs;
  if (!Array.isArray(specs)) return [];
  return specs.filter(isRecord);
}

function dispatchRejectedBeforeSpawn(event: LiveToolEvent): boolean {
  const envelope = dispatchEnvelopeFromOutput(event.output);
  if (envelope?.ok === false) {
    const total = numberFromUnknown(envelope.total ?? envelope.count);
    const successes = Array.isArray(envelope.successes)
      ? envelope.successes.length
      : numberFromUnknown(envelope.success_count);
    if ((total ?? 0) === 0 && (successes ?? 0) === 0) return true;
  }
  if (typeof event.output !== "string") return false;
  return (
    /^\s*\((?:工具失败|tool failed)\)/i.test(event.output) &&
    (/\berror=structured_error\b/i.test(event.output) ||
      /"(?:count|total)"\s*:\s*0/i.test(event.output))
  );
}

function dispatchStatus(status: LiveToolEvent["status"]): AgentTile["status"] {
  if (status === "error") return "error";
  if (status === "done") return "done";
  if (status === "waiting_approval") return "waiting_approval";
  if (status === "running") return "running";
  return "pending";
}
