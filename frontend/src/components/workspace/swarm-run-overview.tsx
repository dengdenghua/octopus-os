"use client";

import {
  BrainCircuitIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  appendControlSessionAction,
  appendControlSessionEvidence,
  ensureControlSession,
  updateControlSessionAction,
} from "@/core/control-session";
import { copyTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";
import { swallow } from "@/core/utils/log";
import { cn } from "@/lib/utils";

import { emitAgentWorkbenchFocus } from "./agent-workbench-events";
import {
  agentRunBadgeClass,
  agentRunDotClass,
  agentRunProgressBarClass,
  agentRunTextClass,
} from "./agent-run-status";
import type { AgentTile } from "./agent-workbench-utils";
import {
  compactDetail,
  durationLabel,
  isRecord,
} from "./agent-workbench-utils";
import type { LiveToolEvent } from "./live-tool-timeline";
import { deriveAgentTilesFromEvents } from "./use-agent-workbench-i18n";

type SwarmOverviewStatus =
  | "running"
  | "waiting_approval"
  | "done"
  | "pending"
  | "error";

type SwarmPhaseId = "dispatch" | "execute" | "aggregate" | "synthesize";

interface SwarmRunPhase {
  id: SwarmPhaseId;
  status: SwarmOverviewStatus;
  count: number;
}

interface SwarmRunSynthesis {
  answeredCount: number;
  primaryAgentId?: string;
  primaryReply?: string;
  supportingCount: number;
  retryCount: number;
  nextAction?: string;
  ready: boolean;
  totalCount: number;
}

interface SwarmRunCapacity {
  requestedMembers: number;
  dispatchedMembers: number;
  droppedMembers: number;
  maxMembers?: number;
  concurrency?: number;
  capacityTier?: string;
}

export interface SwarmRunOverviewModel {
  activePhase: SwarmRunPhase;
  agents: AgentTile[];
  counts: {
    total: number;
    running: number;
    waiting: number;
    done: number;
    error: number;
    pending: number;
  };
  capacity: SwarmRunCapacity;
  currentAgent?: AgentTile;
  eventCount: number;
  evidenceCount: number;
  phases: SwarmRunPhase[];
  progress: number;
  resultCount: number;
  status: SwarmOverviewStatus;
  synthesis?: SwarmRunSynthesis;
  toolCount: number;
}

export interface SwarmReplayPackage {
  schema: "echo.swarm_replay_package.v1";
  exportedAt: string;
  overview: {
    activePhase: SwarmPhaseId;
    capacity: SwarmRunCapacity;
    counts: SwarmRunOverviewModel["counts"];
    evidenceCount: number;
    phaseCount: number;
    progress: number;
    resultCount: number;
    status: SwarmOverviewStatus;
    toolCount: number;
  };
  agents: Array<{
    id: string;
    name: string;
    role?: string;
    status: AgentTile["status"];
    task?: string;
    currentTool?: string;
    filesTouched: string[];
    blackboardWrites: string[];
  }>;
  phases: Array<{
    id: SwarmPhaseId;
    status: SwarmOverviewStatus;
    count: number;
  }>;
  timeline: Array<{
    id: string;
    at: string;
    agentId?: string;
    agentName?: string;
    event: string;
    lifecycle?: LiveToolEvent["lifecycle"];
    parentToolUseId?: string;
    status: LiveToolEvent["status"];
    summary: string;
  }>;
  synthesis?: SwarmRunSynthesis;
  events: LiveToolEvent[];
}

export function buildSwarmRunOverview(
  events: LiveToolEvent[],
): SwarmRunOverviewModel | null {
  const agents = deriveAgentTilesFromEvents(events);
  if (!hasSwarmSignal(events, agents)) return null;
  if (agents.length === 0) return null;

  const capacity = buildSwarmCapacity(events, agents.length);
  const visibleStatusCount =
    agents.filter((agent) => agent.status === "running").length +
    agents.filter((agent) => agent.status === "waiting_approval").length +
    agents.filter((agent) => agent.status === "done").length +
    agents.filter((agent) => agent.status === "error").length +
    agents.filter((agent) => agent.status === "pending").length;
  const inferredPending = Math.max(
    0,
    capacity.dispatchedMembers - visibleStatusCount,
  );
  const counts = {
    total: capacity.dispatchedMembers,
    running: agents.filter((agent) => agent.status === "running").length,
    waiting: agents.filter((agent) => agent.status === "waiting_approval")
      .length,
    done: agents.filter((agent) => agent.status === "done").length,
    error: agents.filter((agent) => agent.status === "error").length,
    pending:
      agents.filter((agent) => agent.status === "pending").length +
      inferredPending,
  };
  const status: SwarmOverviewStatus =
    counts.error > 0
      ? "error"
      : counts.waiting > 0
        ? "waiting_approval"
        : counts.running > 0
          ? "running"
          : counts.pending > 0
            ? "pending"
            : "done";
  const completed = counts.done + counts.error;
  const progress = Math.max(
    status === "running" ? 0.08 : 0,
    Math.min(1, completed / Math.max(1, counts.total)),
  );
  const currentAgent =
    agents.find((agent) => agent.status === "running") ??
    agents.find((agent) => agent.status === "waiting_approval") ??
    agents.find((agent) => agent.status === "pending") ??
    agents[agents.length - 1];
  const resultCount = countResultRows(events);
  const evidenceCount = countEvidence(events, agents);
  const toolCount = events.filter(
    (event) => event.lifecycle === undefined,
  ).length;
  const phases = buildSwarmPhases({
    counts,
    evidenceCount,
    resultCount,
    status,
  });
  const synthesis = buildSwarmSynthesis(events);

  return {
    activePhase: activePhaseFromPhases(phases),
    agents: sortAgentsForOverview(agents),
    capacity,
    counts,
    currentAgent,
    eventCount: events.length,
    evidenceCount,
    phases,
    progress,
    resultCount,
    status,
    synthesis,
    toolCount,
  };
}

export function SwarmRunOverview({
  events,
  className,
  controlSessionId,
}: {
  events: LiveToolEvent[];
  className?: string;
  controlSessionId?: string;
}) {
  const { t } = useI18n();
  const overview = useMemo(() => buildSwarmRunOverview(events), [events]);

  if (!overview) return null;

  const visibleAgents = overview.agents.slice(0, 6);
  const hiddenCount = Math.max(
    0,
    overview.capacity.dispatchedMembers - visibleAgents.length,
  );
  const statusText = statusLabel(overview.status, t.swarmPanel);
  const rhythmLine = buildRhythmLine(overview, t.swarmPanel);

  return (
    <section
      aria-label={t.swarmPanel.title}
      className={cn(
        "mb-2 overflow-hidden rounded-md border border-border-default bg-background/85 text-xs shadow-[var(--shadow-xs)] shadow-black/[0.03]",
        className,
      )}
    >
      <div className="flex items-center gap-2 border-b border-border-subtle px-3 py-2">
        <BrainCircuitIcon className="size-4 shrink-0 text-info dark:text-info" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">
              {t.swarmPanel.title}
            </span>
            <span
              className={cn(
                "rounded-md px-1.5 py-0.5 text-xs font-medium",
                agentRunBadgeClass(overview.status),
              )}
            >
              {statusText}
            </span>
          </div>
          <div className="mt-0.5 truncate text-xs text-muted-foreground">
            {overview.currentAgent
              ? currentAgentLine(overview.currentAgent)
              : t.swarmPanel.noActivity}
          </div>
        </div>
        <div className="hidden items-center gap-3 text-xs text-muted-foreground sm:flex">
          <Stat label={t.swarmPanel.statTotal} value={overview.counts.total} />
          <Stat
            label={t.swarmPanel.statCapacity}
            value={overview.capacity.requestedMembers}
          />
          <Stat
            label={t.swarmPanel.statRunning}
            value={overview.counts.running + overview.counts.waiting}
          />
          <Stat
            label={t.swarmPanel.statCompleted}
            value={overview.counts.done}
          />
          <Stat label={t.swarmPanel.statFailed} value={overview.counts.error} />
          <Stat
            label={t.swarmPanel.statEvidence}
            value={overview.evidenceCount}
          />
        </div>
      </div>

      <div className="h-1 bg-muted/45">
        <div
          className={cn(
            "h-full transition-[width] duration-slow",
            agentRunProgressBarClass(overview.status),
          )}
          style={{ width: `${Math.round(overview.progress * 100)}%` }}
        />
      </div>

      <div className="flex min-w-0 items-center gap-2 border-b border-border-subtle px-3 py-1.5 text-xs">
        <span
          className={cn(
            "size-1.5 shrink-0 rounded-sm",
            agentRunDotClass(overview.activePhase.status),
            overview.activePhase.status === "running" && "animate-pulse",
          )}
        />
        <span className="shrink-0 font-medium text-foreground">
          {phaseLabel(overview.activePhase.id, t.swarmPanel)}
        </span>
        <span className="min-w-0 truncate text-muted-foreground">
          {rhythmLine}
        </span>
      </div>

      <div className="grid grid-cols-4 border-b border-border-subtle bg-muted/15">
        {overview.phases.map((phase) => (
          <div
            key={phase.id}
            className="flex min-w-0 items-center gap-1.5 border-r border-border-subtle px-2 py-1.5 last:border-r-0"
          >
            <span
              className={cn(
                "size-1.5 shrink-0 rounded-sm",
                agentRunDotClass(phase.status),
                phase.status === "running" && "animate-pulse",
              )}
            />
            <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
              {phaseLabel(phase.id, t.swarmPanel)}
            </span>
            <span className="font-mono text-xs text-muted-foreground">
              {phase.count}
            </span>
          </div>
        ))}
      </div>

      {overview.synthesis && (
        <SynthesisStrip
          controlSessionId={controlSessionId}
          events={events}
          overview={overview}
          synthesis={overview.synthesis}
        />
      )}

      <div className="grid gap-1.5 p-2 sm:grid-cols-2">
        {visibleAgents.map((agent) => (
          <button
            key={agent.id}
            type="button"
            className="group flex min-w-0 items-center gap-2 rounded-md border border-border-default bg-muted/20 px-2 py-1.5 text-left transition-colors hover:border-border hover:bg-muted/35"
            onClick={() => emitAgentWorkbenchFocus({ agentId: agent.id })}
          >
            <span
              className={cn(
                "size-2 shrink-0 rounded-sm",
                agentRunDotClass(agent.status),
                agent.status === "running" && "animate-pulse",
              )}
            />
            <span className="min-w-0 flex-1">
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate font-medium text-foreground">
                  {agent.name}
                </span>
                {agent.role && (
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {agent.role}
                  </span>
                )}
              </span>
              <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                {agent.currentTool ?? compactDetail(agent.task, 72)}
              </span>
            </span>
            <span
              className={cn(
                "shrink-0 text-xs font-medium",
                agentRunTextClass(agent.status),
              )}
            >
              {statusLabel(agent.status, t.swarmPanel)}
            </span>
          </button>
        ))}
        {hiddenCount > 0 && (
          <div className="flex items-center justify-center rounded-md border border-dashed border-border-default px-2 py-1.5 text-xs text-muted-foreground">
            +{hiddenCount}
          </div>
        )}
      </div>
    </section>
  );
}

function SynthesisStrip({
  controlSessionId,
  events,
  overview,
  synthesis,
}: {
  controlSessionId?: string;
  events: LiveToolEvent[];
  overview: SwarmRunOverviewModel;
  synthesis: SwarmRunSynthesis;
}) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const [exported, setExported] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const exportTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const status = synthesis.ready
    ? t.swarmPanel.deliveryReady
    : t.swarmPanel.deliveryNeedsReview;
  const copyLabel = copied
    ? t.swarmPanel.deliveryCopied
    : t.swarmPanel.deliveryCopy;
  const exportLabel = exported
    ? t.swarmPanel.deliveryReplayExported
    : t.swarmPanel.deliveryReplayExport;
  const CopyStatusIcon = copied ? CheckIcon : CopyIcon;
  const ExportStatusIcon = exported ? CheckIcon : DownloadIcon;

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      if (exportTimerRef.current) clearTimeout(exportTimerRef.current);
    };
  }, []);

  async function handleCopyPrimaryReply() {
    if (!synthesis.primaryReply) return;
    try {
      await copyTextToClipboard(synthesis.primaryReply);
      setCopied(true);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopied(false), 1400);
    } catch (error) {
      swallow(error, "swarm-delivery-copy");
    }
  }

  async function handleExportReplayPackage() {
    try {
      const replayPackage = buildSwarmReplayPackage(overview, events);
      downloadSwarmReplayPackage(replayPackage);
      await appendSwarmReplayEvidence(controlSessionId, replayPackage);
      setExported(true);
      if (exportTimerRef.current) clearTimeout(exportTimerRef.current);
      exportTimerRef.current = setTimeout(() => setExported(false), 1400);
    } catch (error) {
      swallow(error, "swarm-replay-export");
    }
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border-subtle px-3 py-2 text-xs">
      <span
        className={cn(
          "rounded-sm px-1.5 py-0.5 font-medium",
          synthesis.ready
            ? "bg-success/10 text-success"
            : "bg-warning/10 text-warning",
        )}
      >
        {status}
      </span>
      {synthesis.primaryAgentId && (
        <span className="min-w-0 truncate text-muted-foreground">
          {t.swarmPanel.deliveryPrimary}:{" "}
          <span className="font-medium text-foreground">
            {synthesis.primaryAgentId}
          </span>
        </span>
      )}
      <span className="text-muted-foreground">
        {t.swarmPanel.deliverySupporting(synthesis.supportingCount)}
      </span>
      <span className="text-muted-foreground">
        {t.swarmPanel.deliveryCoverage(
          synthesis.answeredCount,
          synthesis.totalCount,
        )}
      </span>
      {synthesis.retryCount > 0 && (
        <span className="text-warning">
          {t.swarmPanel.deliveryRetry(synthesis.retryCount)}
        </span>
      )}
      {synthesis.nextAction && (
        <span className="min-w-0 flex-1 truncate text-muted-foreground">
          {t.swarmPanel.deliveryNext}:{" "}
          {nextActionLabel(synthesis.nextAction, t.swarmPanel)}
        </span>
      )}
      <button
        type="button"
        aria-label={exportLabel}
        title={exportLabel}
        className="inline-flex size-5 shrink-0 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={handleExportReplayPackage}
      >
        <ExportStatusIcon className="size-3" />
      </button>
      {synthesis.primaryReply && (
        <div className="flex basis-full items-start gap-2 rounded-sm bg-muted/30 px-2 py-1 text-foreground">
          <span className="min-w-0 flex-1">
            <span className="font-medium">
              {t.swarmPanel.deliverySummary}:{" "}
            </span>
            <span>{compactDetail(synthesis.primaryReply, 180)}</span>
          </span>
          <button
            type="button"
            aria-label={copyLabel}
            title={copyLabel}
            className="inline-flex size-5 shrink-0 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-background/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={handleCopyPrimaryReply}
          >
            <CopyStatusIcon className="size-3" />
          </button>
        </div>
      )}
      {synthesis.retryCount > 0 && (
        <span className="basis-full rounded-sm bg-warning/10 px-2 py-1 text-warning">
          {t.swarmPanel.deliveryRetryNote(synthesis.retryCount)}
        </span>
      )}
    </div>
  );
}

async function appendSwarmReplayEvidence(
  controlSessionId: string | undefined,
  replayPackage: SwarmReplayPackage,
) {
  if (!controlSessionId) return;
  const control = {
    sessionId: controlSessionId,
    backendSync: true,
    ownerId: "swarm-overview",
    ownerLabel: "Swarm Overview",
    surface: "backend_preview" as const,
    targetId: "agent-collaboration",
  };
  await ensureControlSession(control, "running", {
    source: "swarm_replay_export",
    schema: replayPackage.schema,
  });
  const actionId =
    `${controlSessionId}-swarm-replay-export-${safeTimestamp(replayPackage.exportedAt)}`.slice(
      0,
      220,
    );
  await appendControlSessionAction(
    control,
    {
      type: "swarm_replay_export",
      schema: replayPackage.schema,
      agent_count: replayPackage.agents.length,
      timeline_count: replayPackage.timeline.length,
    },
    "running",
    actionId,
  );
  await appendControlSessionEvidence(control, {
    id: `${controlSessionId}-swarm-replay-${safeTimestamp(replayPackage.exportedAt)}`,
    actionId,
    kind: "log",
    action: "swarm_replay_export",
    ok: true,
    summary: `swarm replay package exported · ${replayPackage.agents.length} agents · ${replayPackage.timeline.length} events`,
    detail: replayPackage,
  });
  await updateControlSessionAction(control, actionId, "done", {
    schema: replayPackage.schema,
    evidence_id: `${controlSessionId}-swarm-replay-${safeTimestamp(
      replayPackage.exportedAt,
    )}`,
    agent_count: replayPackage.agents.length,
    timeline_count: replayPackage.timeline.length,
  });
}

export function buildSwarmReplayPackage(
  overview: SwarmRunOverviewModel,
  events: LiveToolEvent[],
): SwarmReplayPackage {
  return {
    schema: "echo.swarm_replay_package.v1",
    exportedAt: new Date().toISOString(),
    overview: {
      activePhase: overview.activePhase.id,
      capacity: overview.capacity,
      counts: overview.counts,
      evidenceCount: overview.evidenceCount,
      phaseCount: overview.phases.length,
      progress: overview.progress,
      resultCount: overview.resultCount,
      status: overview.status,
      toolCount: overview.toolCount,
    },
    agents: overview.agents.map((agent) => ({
      id: agent.id,
      name: agent.name,
      role: agent.role,
      status: agent.status,
      task: agent.task,
      currentTool: agent.currentTool,
      filesTouched: [...(agent.filesTouched ?? [])],
      blackboardWrites: [...(agent.blackboardWrites ?? [])],
    })),
    phases: overview.phases.map((phase) => ({
      id: phase.id,
      status: phase.status,
      count: phase.count,
    })),
    timeline: buildSwarmReplayTimeline(events),
    synthesis: overview.synthesis,
    events: events.map((event) => ({ ...event })),
  };
}

function buildSwarmReplayTimeline(events: LiveToolEvent[]) {
  return [...events]
    .sort((a, b) => a.startedAt - b.startedAt)
    .map((event) => ({
      id: event.id,
      at: new Date(event.startedAt).toISOString(),
      agentId: event.agentId,
      agentName: event.agentName ?? event.subagentCodename,
      event: event.name.replace(/^mcp:/, ""),
      lifecycle: event.lifecycle,
      parentToolUseId: event.parentToolUseId,
      status: event.status,
      summary: eventSummary(event),
    }));
}

function eventSummary(event: LiveToolEvent): string {
  const direct =
    event.thought ??
    event.observation ??
    stringFromUnknown(event.output) ??
    stringFromUnknown(event.input?.task) ??
    stringFromUnknown(event.input?.query) ??
    stringFromUnknown(event.input?.path) ??
    stringFromUnknown(event.input?.url);
  if (direct) return compactDetail(direct, 180);
  if (isRecord(event.output)) {
    const outputSummary =
      stringFromUnknown(event.output.summary) ??
      stringFromUnknown(event.output.result) ??
      stringFromUnknown(event.output.reply);
    if (outputSummary) return compactDetail(outputSummary, 180);
  }
  const specs = event.input?.specs;
  if (Array.isArray(specs)) return `${specs.length} member dispatch`;
  return event.lifecycle ?? event.name.replace(/^mcp:/, "");
}

function downloadSwarmReplayPackage(pkg: SwarmReplayPackage) {
  if (typeof document === "undefined" || typeof URL === "undefined") {
    throw new Error("Replay export is only available in a browser");
  }
  const blob = new Blob([JSON.stringify(pkg, null, 2)], {
    type: "application/json",
  });
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = `echo-swarm-replay-${safeTimestamp(pkg.exportedAt)}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

function safeTimestamp(value: string): string {
  return value.replace(/[:.]/g, "-");
}

function hasSwarmSignal(events: LiveToolEvent[], agents: AgentTile[]): boolean {
  if (agents.length > 1) return true;
  return events.some((event) => {
    const eventName = event.name.replace(/^mcp:/, "");
    if (
      eventName === "call_agent_parallel" ||
      eventName === "team_swarm" ||
      eventName === "deep-research-swarm"
    ) {
      return true;
    }
    const specs = event.input?.specs;
    if (Array.isArray(specs) && specs.length > 1) return true;
    const output =
      typeof event.output === "string" ? tryParse(event.output) : event.output;
    if (isRecord(output)) {
      const results = output.results;
      if (Array.isArray(results) && results.length > 1) return true;
    }
    return false;
  });
}

function buildSwarmCapacity(
  events: LiveToolEvent[],
  observedAgents: number,
): SwarmRunCapacity {
  const observedSpecs = maxDispatchSpecCount(events);
  for (const event of [...events].reverse()) {
    const explicit = capacityFromRecord(event.input?.capacity);
    if (explicit)
      return normalizeCapacity(explicit, observedAgents, observedSpecs);
    const output =
      typeof event.output === "string" ? tryParse(event.output) : event.output;
    if (isRecord(output)) {
      const fromResult = capacityFromRecord(output.capacity);
      if (fromResult)
        return normalizeCapacity(fromResult, observedAgents, observedSpecs);
    }
  }
  const total = Math.max(observedAgents, observedSpecs);
  return {
    requestedMembers: total,
    dispatchedMembers: observedAgents,
    droppedMembers: Math.max(0, total - observedAgents),
    capacityTier: capacityTier(observedAgents, total),
  };
}

function capacityFromRecord(value: unknown): SwarmRunCapacity | null {
  if (!isRecord(value)) return null;
  const requested =
    numberFromUnknown(value.requested_members) ??
    numberFromUnknown(value.requestedMembers);
  const dispatched =
    numberFromUnknown(value.dispatched_members) ??
    numberFromUnknown(value.dispatchedMembers) ??
    numberFromUnknown(value.planned_dispatch);
  const dropped =
    numberFromUnknown(value.dropped_members) ??
    numberFromUnknown(value.droppedMembers) ??
    numberFromUnknown(value.planned_dropped);
  if (requested === undefined && dispatched === undefined) return null;
  return {
    requestedMembers: Math.max(0, Math.round(requested ?? dispatched ?? 0)),
    dispatchedMembers: Math.max(0, Math.round(dispatched ?? requested ?? 0)),
    droppedMembers: Math.max(0, Math.round(dropped ?? 0)),
    maxMembers: numberFromUnknown(value.max_members ?? value.maxMembers),
    concurrency: numberFromUnknown(value.concurrency),
    capacityTier: stringFromUnknown(value.capacity_tier ?? value.capacityTier),
  };
}

function normalizeCapacity(
  capacity: SwarmRunCapacity,
  observedAgents: number,
  observedSpecs: number,
): SwarmRunCapacity {
  const requestedMembers = Math.max(
    capacity.requestedMembers,
    capacity.dispatchedMembers,
    observedAgents,
    observedSpecs,
  );
  const dispatchedMembers = Math.max(
    capacity.dispatchedMembers,
    observedAgents,
  );
  return {
    ...capacity,
    requestedMembers,
    dispatchedMembers,
    droppedMembers: Math.max(
      capacity.droppedMembers,
      requestedMembers - dispatchedMembers,
    ),
    capacityTier:
      capacity.capacityTier ??
      capacityTier(dispatchedMembers, requestedMembers),
  };
}

function maxDispatchSpecCount(events: LiveToolEvent[]): number {
  let max = 0;
  for (const event of events) {
    const specs = event.input?.specs;
    if (Array.isArray(specs)) max = Math.max(max, specs.length);
  }
  return max;
}

function capacityTier(dispatchedMembers: number, requestedMembers: number) {
  if (requestedMembers >= 300) return "kimi_scale";
  if (dispatchedMembers >= 64) return "large";
  if (dispatchedMembers >= 16) return "team_scale";
  if (dispatchedMembers >= 2) return "room_scale";
  return "single";
}

function buildSwarmPhases({
  counts,
  evidenceCount,
  resultCount,
  status,
}: {
  counts: SwarmRunOverviewModel["counts"];
  evidenceCount: number;
  resultCount: number;
  status: SwarmOverviewStatus;
}): SwarmRunPhase[] {
  const activeCount = counts.running + counts.waiting + counts.pending;
  const completedCount = counts.done + counts.error;
  const executeStatus: SwarmOverviewStatus =
    counts.error > 0 && activeCount === 0
      ? "error"
      : counts.waiting > 0
        ? "waiting_approval"
        : activeCount > 0
          ? "running"
          : "done";
  const aggregateStatus: SwarmOverviewStatus =
    resultCount > 0 || completedCount > 0
      ? activeCount > 0
        ? "running"
        : status === "error"
          ? "error"
          : "done"
      : "pending";
  const synthesizeStatus: SwarmOverviewStatus =
    resultCount > 0 && activeCount === 0
      ? status === "error"
        ? "error"
        : "done"
      : "pending";
  return [
    {
      id: "dispatch",
      status: counts.total > 0 ? "done" : "pending",
      count: counts.total,
    },
    { id: "execute", status: executeStatus, count: completedCount },
    { id: "aggregate", status: aggregateStatus, count: resultCount },
    { id: "synthesize", status: synthesizeStatus, count: evidenceCount },
  ];
}

function countEvidence(events: LiveToolEvent[], agents: AgentTile[]): number {
  const files = new Set<string>();
  const blackboardWrites = new Set<string>();
  for (const agent of agents) {
    for (const file of agent.filesTouched ?? []) files.add(file);
    for (const key of agent.blackboardWrites ?? []) blackboardWrites.add(key);
  }
  const eventEvidence = events.filter(eventHasEvidence).length;
  return files.size + blackboardWrites.size + eventEvidence;
}

function eventHasEvidence(event: LiveToolEvent): boolean {
  if (event.name === "web_search" || event.name === "fetch_url") return true;
  const path = stringFromUnknown(event.input?.path);
  const query = stringFromUnknown(event.input?.query);
  const url = stringFromUnknown(event.input?.url);
  if (path || query || url) return true;
  const output =
    typeof event.output === "string" ? tryParse(event.output) : event.output;
  if (!isRecord(output)) return Boolean(event.output);
  return [
    "result",
    "results",
    "successes",
    "failures",
    "replies",
    "arbitration",
    "synthesis",
    "summary",
  ].some((key) => output[key] !== undefined);
}

function countResultRows(events: LiveToolEvent[]): number {
  let count = 0;
  for (const event of events) {
    const output =
      typeof event.output === "string" ? tryParse(event.output) : event.output;
    if (!isRecord(output)) continue;
    count += arrayLength(output.results);
    count += arrayLength(output.successes);
    count += arrayLength(output.failures);
    count += arrayLength(output.replies);
    const arbitration = output.arbitration;
    if (isRecord(arbitration)) {
      count += arrayLength(arbitration.outcomes);
    }
    if (isRecord(output.synthesis)) {
      count += 1;
    }
  }
  return count;
}

function buildSwarmSynthesis(
  events: LiveToolEvent[],
): SwarmRunSynthesis | undefined {
  for (const event of [...events].reverse()) {
    const output =
      typeof event.output === "string" ? tryParse(event.output) : event.output;
    if (!isRecord(output)) continue;
    if (isRecord(output.synthesis)) {
      return synthesisFromRecord(output.synthesis);
    }
    const fallback = synthesisFromLegacyEnvelope(output);
    if (fallback) return fallback;
  }
  return undefined;
}

function synthesisFromRecord(
  synthesis: Record<string, unknown>,
): SwarmRunSynthesis {
  const primaryAgentId = stringFromUnknown(synthesis.primary_agent_id);
  const primaryReply = stringFromUnknown(synthesis.primary_reply);
  const supportingCount = arrayLength(synthesis.supporting_agent_ids);
  const retryCount = arrayLength(synthesis.retry_agent_ids);
  const answeredCount =
    numberFromUnknown(synthesis.answered_count) ??
    supportingCount + (primaryAgentId ? 1 : 0);
  const totalCount =
    numberFromUnknown(synthesis.total_count) ??
    Math.max(answeredCount + retryCount, answeredCount, retryCount);
  const nextAction = stringFromUnknown(synthesis.recommended_next_action);
  return {
    answeredCount,
    primaryAgentId,
    primaryReply,
    supportingCount,
    retryCount,
    nextAction,
    ready: synthesis.ready === true,
    totalCount,
  };
}

function synthesisFromLegacyEnvelope(
  output: Record<string, unknown>,
): SwarmRunSynthesis | undefined {
  const arbitration = isRecord(output.arbitration) ? output.arbitration : null;
  const replies = Array.isArray(output.replies)
    ? output.replies.filter(isRecord)
    : [];
  if (!arbitration || replies.length === 0) return undefined;
  const primaryAgentId = stringFromUnknown(arbitration.primary_agent_id);
  const answeredIds = arrayOfStrings(arbitration.answered_agent_ids);
  const failedIds = arrayOfStrings(arbitration.failed_agent_ids);
  const emptyIds = arrayOfStrings(arbitration.empty_agent_ids);
  const primaryReply = primaryAgentId
    ? stringFromUnknown(
        replies.find(
          (reply) => stringFromUnknown(reply.agent_id) === primaryAgentId,
        )?.reply,
      )
    : undefined;
  const retryCount = failedIds.length + emptyIds.length;
  const supportingCount = answeredIds.filter(
    (id) => id !== primaryAgentId,
  ).length;
  const answeredCount = answeredIds.length;
  return {
    answeredCount,
    primaryAgentId,
    primaryReply,
    supportingCount,
    retryCount,
    nextAction: stringFromUnknown(arbitration.recommended_next_action),
    ready: Boolean(primaryAgentId && primaryReply && retryCount === 0),
    totalCount: Math.max(replies.length, answeredCount + retryCount),
  };
}

function arrayLength(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function arrayOfStrings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is string =>
          typeof item === "string" && item.trim().length > 0,
      )
    : [];
}

function activePhaseFromPhases(phases: SwarmRunPhase[]): SwarmRunPhase {
  const fallback: SwarmRunPhase = {
    id: "dispatch",
    status: "pending",
    count: 0,
  };
  return (
    phases.find((phase) => phase.status === "running") ??
    phases.find((phase) => phase.status === "waiting_approval") ??
    phases.find((phase) => phase.status === "pending") ??
    phases[phases.length - 1] ??
    fallback
  );
}

function sortAgentsForOverview(agents: AgentTile[]): AgentTile[] {
  const rank: Record<AgentTile["status"], number> = {
    running: 0,
    waiting_approval: 1,
    error: 2,
    pending: 3,
    done: 4,
  };
  return [...agents].sort((a, b) => {
    const byStatus = rank[a.status] - rank[b.status];
    if (byStatus !== 0) return byStatus;
    return a.startedAt - b.startedAt;
  });
}

function statusLabel(
  status: AgentTile["status"] | SwarmOverviewStatus,
  labels: ReturnType<typeof useI18n>["t"]["swarmPanel"],
): string {
  if (status === "running") return labels.panelStatusRunning;
  if (status === "waiting_approval") return labels.status.pending;
  if (status === "done") return labels.panelStatusCompleted;
  if (status === "error") return labels.panelStatusFailed;
  return labels.panelStatusPending;
}

function phaseLabel(
  phase: SwarmPhaseId,
  labels: ReturnType<typeof useI18n>["t"]["swarmPanel"],
): string {
  if (phase === "dispatch") return labels.phaseDispatch;
  if (phase === "execute") return labels.phaseExecute;
  if (phase === "aggregate") return labels.phaseAggregate;
  return labels.phaseSynthesize;
}

function nextActionLabel(
  action: string,
  labels: ReturnType<typeof useI18n>["t"]["swarmPanel"],
): string {
  if (action === "use_primary_response") {
    return labels.deliveryActionUsePrimary;
  }
  if (action === "use_primary_and_retry_failed_members") {
    return labels.deliveryActionUsePrimaryAndRetry;
  }
  if (action === "ask_members_to_expand") {
    return labels.deliveryActionAskMembers;
  }
  if (action === "retry_or_fallback_to_single_agent") {
    return labels.deliveryActionRetryOrFallback;
  }
  if (action === "fallback_to_single_agent") {
    return labels.deliveryActionFallback;
  }
  return action.replace(/_/g, " ");
}

function buildRhythmLine(
  overview: SwarmRunOverviewModel,
  labels: ReturnType<typeof useI18n>["t"]["swarmPanel"],
): string {
  const done = overview.counts.done + overview.counts.error;
  const total = Math.max(1, overview.counts.total);
  const progressLabel = labels.rhythmProgress(done, total);
  const evidenceLabel = labels.rhythmEvidence(overview.evidenceCount);
  const resultLabel = labels.rhythmResults(overview.resultCount);
  if (overview.status === "running" || overview.status === "waiting_approval") {
    const active = overview.currentAgent?.name
      ? labels.rhythmActive(overview.currentAgent.name)
      : labels.rhythmWorking;
    return [active, progressLabel, evidenceLabel].join(" · ");
  }
  if (overview.status === "error") {
    return [labels.rhythmNeedsReview, progressLabel, resultLabel].join(" · ");
  }
  return [
    labels.rhythmDelivered,
    progressLabel,
    evidenceLabel,
    resultLabel,
  ].join(" · ");
}

function currentAgentLine(agent: AgentTile): string {
  const parts = [
    agent.name,
    agent.currentTool,
    agent.durationMs !== undefined ? durationLabel(agent.durationMs) : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span>{label}</span>
      <span className="font-mono font-medium text-foreground">{value}</span>
    </span>
  );
}

function tryParse(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed || !/^[{[]/.test(trimmed)) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function stringFromUnknown(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function numberFromUnknown(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}
